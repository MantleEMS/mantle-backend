"""
Unit tests for ThreadAgent — prompt construction, context loading,
skip conditions, conversation formatting, and audit logging.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.llm_client import AgentResult, LLMConfig, LLMClient
from app.agent.thread_agent import (
    ThreadAgent,
    build_thread_agent_prompt,
    THREAD_AGENT_TOOLS,
    THREAD_AGENT_MAX_ITERATIONS,
    _extract_escalation_rules,
    _extract_resolution_conditions,
    _extract_notification_rules,
)
from app.tools.registry import ToolRegistry, ToolDefinition


INC_ID = uuid.uuid4()
ORG_ID = uuid.uuid4()
SOP_ID = uuid.uuid4()
MSG_ID = uuid.uuid4()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_registry():
    """Build a registry with the 3 tools the thread agent uses."""
    registry = ToolRegistry()
    for name in THREAD_AGENT_TOOLS:
        registry.register(ToolDefinition(
            name=name,
            description=f"mock {name}",
            parameters={"type": "object", "properties": {}},
            handler=AsyncMock(return_value={"ok": True}),
            category="action",
        ))
    # Register an extra tool to verify subset filtering
    registry.register(ToolDefinition(
        name="dispatch_responder",
        description="should NOT be in subset",
        parameters={"type": "object", "properties": {}},
        handler=AsyncMock(),
        category="action",
    ))
    return registry


def _make_agent(registry=None):
    config = LLMConfig(
        provider="ollama", model="test-model", temperature=0.0,
        max_tokens=1024, timeout_seconds=30, num_ctx=8192,
    )
    llm = LLMClient(config)
    reg = registry or _make_registry()
    return ThreadAgent(llm, reg)


def _make_mock_incident(status="active", emergency_type="workplace_violence"):
    inc = MagicMock()
    inc.id = INC_ID
    inc.org_id = ORG_ID
    inc.status = status
    inc.emergency_type = emergency_type
    inc.severity = 3
    inc.sop_id = SOP_ID
    return inc


def _make_mock_sop(emergency_type="workplace_violence", sop_code="SOP-WV-001"):
    sop = MagicMock()
    sop.id = SOP_ID
    sop.name = "Workplace Violence Response"
    sop.sop_code = sop_code
    sop.emergency_type = emergency_type
    sop.steps = [
        {"step": 1, "actor": "ai", "action": "begin_recording", "auto": True,
         "tier": "green", "description": "Start recording"},
    ]
    sop.responder_checklist = [
        "Assess scene safety from outside",
        "Identify and de-escalate threat source",
        "Ensure worker safety, escort to vehicle",
        "Report status to commander",
    ]
    return sop


def _make_mock_message(content="He has a knife!", sender_type="human", msg_id=None):
    msg = MagicMock()
    msg.id = msg_id or MSG_ID
    msg.incident_id = INC_ID
    msg.sender_type = sender_type
    msg.message_type = "text"
    msg.content = content
    msg.meta = {}
    msg.seq = 1
    msg.created_at = datetime(2026, 3, 27, 14, 30, 0, tzinfo=timezone.utc)
    return msg


def _make_mock_action(action_type="dispatch_responder", tier="red"):
    action = MagicMock()
    action.id = uuid.uuid4()
    action.incident_id = INC_ID
    action.tier = tier
    action.action_type = action_type
    action.status = "pending"
    action.description = f"Pending {action_type}"
    return action


def _patch_db(incident, sop, messages, new_message, actions):
    """Patch AsyncSessionLocal to return controlled query results."""
    mock_db = AsyncMock()

    # The on_message method runs 4 queries: incident, sop, messages (desc), new_message, actions
    inc_result = MagicMock()
    inc_result.scalar_one_or_none.return_value = incident

    sop_result = MagicMock()
    sop_result.scalar_one_or_none.return_value = sop

    msg_scalars = MagicMock()
    msg_scalars.all.return_value = messages
    msg_result = MagicMock()
    msg_result.scalars.return_value = msg_scalars

    new_msg_result = MagicMock()
    new_msg_result.scalar_one_or_none.return_value = new_message

    action_scalars = MagicMock()
    action_scalars.all.return_value = actions
    action_result = MagicMock()
    action_result.scalars.return_value = action_scalars

    mock_db.execute = AsyncMock(
        side_effect=[inc_result, sop_result, msg_result, new_msg_result, action_result]
    )
    mock_db.commit = AsyncMock()
    return mock_db


# ── Tool subset ──────────────────────────────────────────────────────────────

def test_thread_agent_uses_tool_subset():
    """ThreadAgent should only have 3 tools, not the full registry."""
    agent = _make_agent()
    tools = agent.registry.get_all()
    tool_names = {t.name for t in tools}
    assert tool_names == set(THREAD_AGENT_TOOLS)
    assert "dispatch_responder" not in tool_names


def test_max_iterations_is_3():
    assert THREAD_AGENT_MAX_ITERATIONS == 3


# ── Prompt construction ──────────────────────────────────────────────────────

def test_prompt_includes_sop_code_and_emergency_type():
    sop = {
        "sop_code": "SOP-WV-001",
        "name": "Workplace Violence Response",
        "emergency_type": "workplace_violence",
        "steps": [],
        "responder_checklist": ["Assess scene safety"],
    }
    incident = {"emergency_type": "workplace_violence", "severity": 4, "status": "active"}
    prompt = build_thread_agent_prompt(sop, incident)

    assert "SOP-WV-001" in prompt
    assert "Workplace Violence Response" in prompt
    assert "workplace_violence" in prompt


def test_prompt_includes_responder_checklist():
    sop = {
        "sop_code": "SOP-MED-001",
        "name": "Medical Emergency",
        "emergency_type": "medical",
        "steps": [],
        "responder_checklist": [
            "Check airway, breathing, circulation",
            "Control visible bleeding",
        ],
    }
    incident = {"emergency_type": "medical", "severity": 3, "status": "active"}
    prompt = build_thread_agent_prompt(sop, incident)

    assert "Check airway, breathing, circulation" in prompt
    assert "Control visible bleeding" in prompt


def test_prompt_violence_sop_warns_no_emergency_contact():
    sop = {
        "sop_code": "SOP-WV-001",
        "name": "Workplace Violence",
        "emergency_type": "workplace_violence",
        "steps": [],
        "responder_checklist": [],
    }
    incident = {"emergency_type": "workplace_violence", "severity": 3, "status": "active"}
    prompt = build_thread_agent_prompt(sop, incident)

    assert "Do NOT notify the patient's emergency contact" in prompt


def test_prompt_medical_sop_recommends_emergency_contact():
    sop = {
        "sop_code": "SOP-MED-001",
        "name": "Medical Emergency",
        "emergency_type": "medical",
        "steps": [],
        "responder_checklist": [],
    }
    incident = {"emergency_type": "medical", "severity": 3, "status": "active"}
    prompt = build_thread_agent_prompt(sop, incident)

    assert "DO recommend notifying the patient's emergency contact" in prompt


def test_prompt_includes_severity():
    sop = {
        "sop_code": "SOP-WV-001", "name": "WV", "emergency_type": "workplace_violence",
        "steps": [], "responder_checklist": [],
    }
    incident = {"emergency_type": "workplace_violence", "severity": 5, "status": "active"}
    prompt = build_thread_agent_prompt(sop, incident)
    assert "Current severity: 5" in prompt


def test_prompt_out_of_scope_rules():
    sop = {
        "sop_code": "SOP-WV-001", "name": "WV", "emergency_type": "workplace_violence",
        "steps": [], "responder_checklist": [],
    }
    incident = {"emergency_type": "workplace_violence", "severity": 3, "status": "active"}
    prompt = build_thread_agent_prompt(sop, incident)

    assert "Never provide medical advice" in prompt
    assert "Never instruct the worker to confront" in prompt
    assert "Never recommend the responder enter an unsecured scene" in prompt
    assert "Never override a commander decision" in prompt


# ── Extraction helpers ───────────────────────────────────────────────────────

def test_extract_escalation_rules_from_dict():
    steps = {
        "escalation_rules": [
            {"trigger": "weapon_mentioned", "actions": ["upgrade_severity_5"], "applies_to": ["workplace_violence"]},
        ]
    }
    result = _extract_escalation_rules(steps)
    assert "weapon_mentioned" in result
    assert "upgrade_severity_5" in result
    assert "workplace_violence" in result


def test_extract_escalation_rules_empty():
    result = _extract_escalation_rules([])
    assert "No additional escalation rules" in result


def test_extract_resolution_conditions():
    steps = {
        "resolution_conditions": [
            "responder_confirms_scene_clear",
            {"if": "weapon_reported", "then": "weapon_secured_confirmed"},
        ]
    }
    result = _extract_resolution_conditions(steps)
    assert "responder_confirms_scene_clear" in result
    assert "weapon_reported" in result
    assert "weapon_secured_confirmed" in result


def test_extract_notification_rules():
    steps = {
        "notification_rules": [
            {"notify": "emergency_contact", "when": "medical", "not_when": "workplace_violence"},
        ]
    }
    result = _extract_notification_rules(steps)
    assert "emergency_contact" in result
    assert "medical" in result
    assert "workplace_violence" in result


# ── Skip conditions ──────────────────────────────────────────────────────────

async def test_skip_resolved_incident():
    agent = _make_agent()
    incident = _make_mock_incident(status="resolved")

    with patch("app.agent.thread_agent.AsyncSessionLocal") as mock_session:
        mock_db = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        inc_result = MagicMock()
        inc_result.scalar_one_or_none.return_value = incident
        mock_db.execute = AsyncMock(return_value=inc_result)

        # Should NOT call the LLM
        agent.llm.run_agent = AsyncMock()
        await agent.on_message(INC_ID, MSG_ID)
        agent.llm.run_agent.assert_not_awaited()


async def test_skip_cancelled_incident():
    agent = _make_agent()
    incident = _make_mock_incident(status="cancelled")

    with patch("app.agent.thread_agent.AsyncSessionLocal") as mock_session:
        mock_db = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        inc_result = MagicMock()
        inc_result.scalar_one_or_none.return_value = incident
        mock_db.execute = AsyncMock(return_value=inc_result)

        agent.llm.run_agent = AsyncMock()
        await agent.on_message(INC_ID, MSG_ID)
        agent.llm.run_agent.assert_not_awaited()


async def test_skip_missing_incident():
    agent = _make_agent()

    with patch("app.agent.thread_agent.AsyncSessionLocal") as mock_session:
        mock_db = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        inc_result = MagicMock()
        inc_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=inc_result)

        agent.llm.run_agent = AsyncMock()
        await agent.on_message(INC_ID, MSG_ID)
        agent.llm.run_agent.assert_not_awaited()


async def test_skip_no_sop():
    agent = _make_agent()
    incident = _make_mock_incident()
    incident.sop_id = None

    with patch("app.agent.thread_agent.AsyncSessionLocal") as mock_session:
        mock_db = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        inc_result = MagicMock()
        inc_result.scalar_one_or_none.return_value = incident
        mock_db.execute = AsyncMock(return_value=inc_result)

        agent.llm.run_agent = AsyncMock()
        await agent.on_message(INC_ID, MSG_ID)
        agent.llm.run_agent.assert_not_awaited()


# ── Full invocation ──────────────────────────────────────────────────────────

async def test_on_message_calls_llm_with_correct_params():
    """Verify the agent loads context, builds prompt, and calls LLM."""
    agent = _make_agent()
    incident = _make_mock_incident()
    sop = _make_mock_sop()
    message = _make_mock_message("He has a knife!")
    messages = [message]

    captured = {}

    async def fake_run_agent(system_prompt, messages, registry, max_iterations=3):
        captured["system_prompt"] = system_prompt
        captured["messages"] = messages
        captured["max_iterations"] = max_iterations
        captured["registry"] = registry
        return AgentResult(final_text="Weapon detected.", trace=[], iterations=1, success=True)

    agent.llm.run_agent = fake_run_agent

    with patch("app.agent.thread_agent.AsyncSessionLocal") as mock_session, \
         patch("app.agent.thread_agent.write_audit", new_callable=AsyncMock), \
         patch("app.agent.thread_agent.create_message", new_callable=AsyncMock) as mock_create:

        mock_db = _patch_db(incident, sop, messages, message, [])
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        await agent.on_message(INC_ID, MSG_ID)

    # Verify system prompt has SOP-specific content
    assert "SOP-WV-001" in captured["system_prompt"]
    assert "workplace_violence" in captured["system_prompt"]
    assert "Assess scene safety" in captured["system_prompt"]

    # Verify max_iterations is 3
    assert captured["max_iterations"] == 3

    # Verify the user message contains the thread history
    user_content = captured["messages"][0]["content"]
    assert "He has a knife!" in user_content


async def test_on_message_includes_pending_actions_in_context():
    agent = _make_agent()
    incident = _make_mock_incident()
    sop = _make_mock_sop()
    message = _make_mock_message("I'm on scene")
    action = _make_mock_action("dispatch_responder", "red")

    captured = {}

    async def fake_run_agent(system_prompt, messages, registry, max_iterations=3):
        captured["messages"] = messages
        return AgentResult(final_text="ok", trace=[], iterations=1, success=True)

    agent.llm.run_agent = fake_run_agent

    with patch("app.agent.thread_agent.AsyncSessionLocal") as mock_session, \
         patch("app.agent.thread_agent.write_audit", new_callable=AsyncMock), \
         patch("app.agent.thread_agent.create_message", new_callable=AsyncMock):

        mock_db = _patch_db(incident, sop, [message], message, [action])
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        await agent.on_message(INC_ID, MSG_ID)

    user_content = captured["messages"][0]["content"]
    assert "PENDING ACTIONS" in user_content
    assert "dispatch_responder" in user_content


async def test_on_message_writes_audit_trace():
    agent = _make_agent()
    incident = _make_mock_incident()
    sop = _make_mock_sop()
    message = _make_mock_message("acknowledged")

    agent.llm.run_agent = AsyncMock(
        return_value=AgentResult(
            final_text="",
            trace=[{"tool": "post_thread_message", "params": {"content": "Noted."}, "result": "{}"}],
            iterations=1,
            success=True,
        )
    )

    with patch("app.agent.thread_agent.AsyncSessionLocal") as mock_session, \
         patch("app.agent.thread_agent.write_audit", new_callable=AsyncMock) as mock_audit:

        mock_db = _patch_db(incident, sop, [message], message, [])
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        await agent.on_message(INC_ID, MSG_ID)

    mock_audit.assert_awaited_once()
    call_kwargs = mock_audit.call_args.kwargs
    assert call_kwargs["event_type"] == "thread_agent.completed"
    assert call_kwargs["actor_type"] == "ai"
    assert call_kwargs["detail"]["success"] is True
    assert len(call_kwargs["detail"]["tool_calls"]) == 1
    assert call_kwargs["detail"]["tool_calls"][0]["tool"] == "post_thread_message"


async def test_on_message_exception_does_not_propagate():
    """Thread agent runs in background — exceptions must be caught, not raised."""
    agent = _make_agent()

    with patch("app.agent.thread_agent.AsyncSessionLocal") as mock_session:
        mock_session.return_value.__aenter__ = AsyncMock(
            side_effect=Exception("DB connection failed")
        )
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        # Should not raise
        await agent.on_message(INC_ID, MSG_ID)


# ── Conversation formatting ──────────────────────────────────────────────────

def test_format_thread_includes_timestamps():
    agent = _make_agent()
    msg = _make_mock_message("Help me!", sender_type="human")
    result = agent._format_thread_as_conversation([msg], [], msg)
    content = result[0]["content"]
    assert "14:30:00" in content
    assert "HUMAN" in content
    assert "Help me!" in content


def test_format_thread_notes_attachments():
    agent = _make_agent()
    msg = _make_mock_message("See this")
    msg.meta = {"attachment_type": "photo", "attachment_url": "https://example.com/photo.jpg"}
    result = agent._format_thread_as_conversation([msg], [], msg)
    content = result[0]["content"]
    assert "attached photo" in content


def test_format_thread_new_message_section():
    agent = _make_agent()
    msg = _make_mock_message("Weapon secured")
    result = agent._format_thread_as_conversation([], [], msg)
    content = result[0]["content"]
    assert "NEW MESSAGE" in content
    assert "Weapon secured" in content


# ── SOP differentiation ─────────────────────────────────────────────────────

def test_violence_sop_prompt_differs_from_medical():
    """Same weapon scenario should produce different prompt constraints per SOP."""
    wv_sop = {
        "sop_code": "SOP-WV-001", "name": "Workplace Violence",
        "emergency_type": "workplace_violence",
        "steps": [], "responder_checklist": [],
    }
    med_sop = {
        "sop_code": "SOP-MED-001", "name": "Medical Emergency",
        "emergency_type": "medical",
        "steps": [], "responder_checklist": [],
    }
    incident = {"emergency_type": "workplace_violence", "severity": 3, "status": "active"}

    wv_prompt = build_thread_agent_prompt(wv_sop, incident)
    med_prompt = build_thread_agent_prompt(med_sop, {**incident, "emergency_type": "medical"})

    # Violence SOP should upgrade to armed response
    assert "armed law enforcement" in wv_prompt
    assert "workplace_violence" in wv_prompt

    # Medical SOP should recommend reclassification, not direct armed response
    assert "recommend the commander switch" in med_prompt
    assert "medical" in med_prompt

    # Both should have the out-of-scope rules
    assert "Never override a commander decision" in wv_prompt
    assert "Never override a commander decision" in med_prompt


# ── Auto-post final_text ─────────────────────────────────────────────────────

async def test_final_text_posted_when_no_post_thread_message_tool_call():
    """If LLM returns text but never called post_thread_message, auto-post it."""
    agent = _make_agent()
    incident = _make_mock_incident()
    sop = _make_mock_sop()
    message = _make_mock_message("Is anyone on the way?")

    # LLM returns text with no tool calls
    agent.llm.run_agent = AsyncMock(
        return_value=AgentResult(
            final_text="A responder has been dispatched and is en route.",
            trace=[],
            iterations=1,
            success=True,
        )
    )

    with patch("app.agent.thread_agent.AsyncSessionLocal") as mock_session, \
         patch("app.agent.thread_agent.write_audit", new_callable=AsyncMock), \
         patch("app.agent.thread_agent.create_message", new_callable=AsyncMock) as mock_create:

        mock_db = _patch_db(incident, sop, [message], message, [])
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        await agent.on_message(INC_ID, MSG_ID)

    # Should have called create_message to post the text
    mock_create.assert_awaited_once()
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["sender_type"] == "ai"
    assert call_kwargs["message_type"] == "text"
    assert "responder has been dispatched" in call_kwargs["content"]


async def test_final_text_not_double_posted_when_tool_already_posted():
    """If LLM already called post_thread_message, don't double-post."""
    agent = _make_agent()
    incident = _make_mock_incident()
    sop = _make_mock_sop()
    message = _make_mock_message("He has a knife!")

    # LLM called post_thread_message AND returned final_text
    agent.llm.run_agent = AsyncMock(
        return_value=AgentResult(
            final_text="Summary text.",
            trace=[{"tool": "post_thread_message", "params": {"content": "Weapon detected."}, "result": "{}"}],
            iterations=2,
            success=True,
        )
    )

    with patch("app.agent.thread_agent.AsyncSessionLocal") as mock_session, \
         patch("app.agent.thread_agent.write_audit", new_callable=AsyncMock), \
         patch("app.agent.thread_agent.create_message", new_callable=AsyncMock) as mock_create:

        mock_db = _patch_db(incident, sop, [message], message, [])
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        await agent.on_message(INC_ID, MSG_ID)

    # Should NOT have called create_message — post_thread_message already did it
    mock_create.assert_not_awaited()


async def test_empty_final_text_not_posted():
    """If LLM returns empty text, don't post an empty message."""
    agent = _make_agent()
    incident = _make_mock_incident()
    sop = _make_mock_sop()
    message = _make_mock_message("acknowledged")

    agent.llm.run_agent = AsyncMock(
        return_value=AgentResult(
            final_text="",
            trace=[],
            iterations=1,
            success=True,
        )
    )

    with patch("app.agent.thread_agent.AsyncSessionLocal") as mock_session, \
         patch("app.agent.thread_agent.write_audit", new_callable=AsyncMock), \
         patch("app.agent.thread_agent.create_message", new_callable=AsyncMock) as mock_create:

        mock_db = _patch_db(incident, sop, [message], message, [])
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        await agent.on_message(INC_ID, MSG_ID)

    mock_create.assert_not_awaited()
