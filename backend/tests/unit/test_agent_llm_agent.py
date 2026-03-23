"""Unit tests for LLMAgent — prompt construction, execution, audit logging."""

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent.llm_agent import LLMAgent
from app.agent.llm_client import AgentResult, LLMConfig, LLMClient
from app.tools.registry import ToolRegistry


INC_ID = uuid.uuid4()
ORG_ID = uuid.uuid4()
SOP_ID = uuid.uuid4()

SAMPLE_SOP = {
    "id": str(SOP_ID),
    "name": "Medical Emergency",
    "emergency_type": "medical",
    "steps": [
        {"step": 1, "actor": "ai", "action": "begin_recording", "auto": True,
         "description": "Start recording", "tier": "green"},
    ],
    "responder_checklist": [],
}


def _make_agent(adaptive=False):
    config = LLMConfig(provider="ollama", model="test-model", temperature=0.0,
                       max_tokens=1024, timeout_seconds=30, num_ctx=8192)
    llm = LLMClient(config)
    registry = ToolRegistry()
    agent = LLMAgent(llm, registry)
    return agent


def _make_mock_incident(emergency_type="medical"):
    inc = MagicMock()
    inc.id = INC_ID
    inc.status = "triggered"
    inc.emergency_type = emergency_type
    inc.trigger_source = "panic_button"
    inc.facility_id = uuid.uuid4()
    inc.commander_id = uuid.uuid4()
    inc.org_id = ORG_ID
    return inc


def _make_mock_sop():
    sop = MagicMock()
    sop.id = SOP_ID
    sop.name = SAMPLE_SOP["name"]
    sop.emergency_type = SAMPLE_SOP["emergency_type"]
    sop.steps = SAMPLE_SOP["steps"]
    sop.responder_checklist = []
    return sop


# ── System prompt construction ────────────────────────────────────────────────

async def test_llm_agent_builds_prompt_with_sop_name():
    agent = _make_agent()
    incident = _make_mock_incident()
    sop = _make_mock_sop()

    captured_prompt = {}

    async def fake_run_agent(system_prompt, messages, registry, max_iterations=15):
        captured_prompt["system"] = system_prompt
        captured_prompt["user"] = messages[0]["content"]
        return AgentResult(final_text="done", success=True, iterations=1)

    agent.llm.run_agent = fake_run_agent

    with patch("app.agent.llm_agent.AsyncSessionLocal") as mock_session, \
         patch("app.agent.llm_agent.write_audit", new_callable=AsyncMock), \
         patch("app.agent.llm_agent.settings") as mock_settings:

        mock_settings.LLM_ADAPTIVE_SOP = False
        mock_settings.LLM_MODEL = "test-model"
        mock_settings.LLM_PROVIDER = "ollama"

        mock_db = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        inc_result = MagicMock()
        inc_result.scalar_one_or_none.return_value = incident
        sop_result = MagicMock()
        sop_result.scalar_one_or_none.return_value = sop
        mock_db.execute = AsyncMock(side_effect=[inc_result, sop_result])
        mock_db.commit = AsyncMock()

        await agent.execute(INC_ID, ORG_ID, SOP_ID)

    assert "Medical Emergency" in captured_prompt["system"]
    assert str(INC_ID) in captured_prompt["user"]
    assert "medical" in captured_prompt["user"]


async def test_llm_agent_adaptive_false_excludes_adaptive_section():
    agent = _make_agent()
    incident = _make_mock_incident()
    sop = _make_mock_sop()

    captured = {}

    async def fake_run_agent(system_prompt, messages, registry, max_iterations=15):
        captured["system"] = system_prompt
        return AgentResult(final_text="done", success=True, iterations=1)

    agent.llm.run_agent = fake_run_agent

    with patch("app.agent.llm_agent.AsyncSessionLocal") as mock_session, \
         patch("app.agent.llm_agent.write_audit", new_callable=AsyncMock), \
         patch("app.agent.llm_agent.settings") as mock_settings:

        mock_settings.LLM_ADAPTIVE_SOP = False
        mock_settings.LLM_MODEL = "test-model"
        mock_settings.LLM_PROVIDER = "ollama"

        mock_db = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        inc_result = MagicMock()
        inc_result.scalar_one_or_none.return_value = incident
        sop_result = MagicMock()
        sop_result.scalar_one_or_none.return_value = sop
        mock_db.execute = AsyncMock(side_effect=[inc_result, sop_result])
        mock_db.commit = AsyncMock()

        await agent.execute(INC_ID, ORG_ID, SOP_ID)

    assert "propose_step_adaptation" not in captured["system"]
    assert "Adaptive SOP" not in captured["system"]


async def test_llm_agent_adaptive_true_includes_adaptive_section():
    agent = _make_agent()
    incident = _make_mock_incident()
    sop = _make_mock_sop()

    captured = {}

    async def fake_run_agent(system_prompt, messages, registry, max_iterations=15):
        captured["system"] = system_prompt
        return AgentResult(final_text="done", success=True, iterations=1)

    agent.llm.run_agent = fake_run_agent

    with patch("app.agent.llm_agent.AsyncSessionLocal") as mock_session, \
         patch("app.agent.llm_agent.write_audit", new_callable=AsyncMock), \
         patch("app.agent.llm_agent.settings") as mock_settings:

        mock_settings.LLM_ADAPTIVE_SOP = True
        mock_settings.LLM_MODEL = "test-model"
        mock_settings.LLM_PROVIDER = "ollama"

        mock_db = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        inc_result = MagicMock()
        inc_result.scalar_one_or_none.return_value = incident
        sop_result = MagicMock()
        sop_result.scalar_one_or_none.return_value = sop
        mock_db.execute = AsyncMock(side_effect=[inc_result, sop_result])
        mock_db.commit = AsyncMock()

        await agent.execute(INC_ID, ORG_ID, SOP_ID)

    assert "propose_step_adaptation" in captured["system"]
    assert "Adaptive SOP" in captured["system"]


# ── Incident status transition ────────────────────────────────────────────────

async def test_llm_agent_marks_incident_active():
    agent = _make_agent()
    incident = _make_mock_incident()
    incident.status = "triggered"
    sop = _make_mock_sop()

    agent.llm.run_agent = AsyncMock(
        return_value=AgentResult(final_text="done", success=True, iterations=1)
    )

    with patch("app.agent.llm_agent.AsyncSessionLocal") as mock_session, \
         patch("app.agent.llm_agent.write_audit", new_callable=AsyncMock), \
         patch("app.agent.llm_agent.settings") as mock_settings:

        mock_settings.LLM_ADAPTIVE_SOP = False
        mock_settings.LLM_MODEL = "test-model"
        mock_settings.LLM_PROVIDER = "ollama"

        mock_db = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        inc_result = MagicMock()
        inc_result.scalar_one_or_none.return_value = incident
        sop_result = MagicMock()
        sop_result.scalar_one_or_none.return_value = sop
        mock_db.execute = AsyncMock(side_effect=[inc_result, sop_result])
        mock_db.commit = AsyncMock()

        await agent.execute(INC_ID, ORG_ID, SOP_ID)

    assert incident.status == "active"
    mock_db.commit.assert_awaited()


async def test_llm_agent_skips_status_update_if_already_active():
    agent = _make_agent()
    incident = _make_mock_incident()
    incident.status = "active"  # already active
    sop = _make_mock_sop()

    agent.llm.run_agent = AsyncMock(
        return_value=AgentResult(final_text="done", success=True, iterations=1)
    )

    with patch("app.agent.llm_agent.AsyncSessionLocal") as mock_session, \
         patch("app.agent.llm_agent.write_audit", new_callable=AsyncMock), \
         patch("app.agent.llm_agent.settings") as mock_settings:

        mock_settings.LLM_ADAPTIVE_SOP = False
        mock_settings.LLM_MODEL = "test-model"
        mock_settings.LLM_PROVIDER = "ollama"

        mock_db = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        inc_result = MagicMock()
        inc_result.scalar_one_or_none.return_value = incident
        sop_result = MagicMock()
        sop_result.scalar_one_or_none.return_value = sop
        mock_db.execute = AsyncMock(side_effect=[inc_result, sop_result])
        mock_db.commit = AsyncMock()

        await agent.execute(INC_ID, ORG_ID, SOP_ID)

    # Status shouldn't be changed, no commit needed for status
    assert incident.status == "active"


# ── Audit logging ─────────────────────────────────────────────────────────────

async def test_llm_agent_writes_audit_on_success():
    agent = _make_agent()
    incident = _make_mock_incident()
    sop = _make_mock_sop()

    agent.llm.run_agent = AsyncMock(
        return_value=AgentResult(
            final_text="SOP complete.",
            trace=[{"tool": "get_patient_info", "params": {}, "result": "{}"}],
            iterations=2,
            success=True,
        )
    )

    with patch("app.agent.llm_agent.AsyncSessionLocal") as mock_session, \
         patch("app.agent.llm_agent.write_audit", new_callable=AsyncMock) as mock_audit, \
         patch("app.agent.llm_agent.settings") as mock_settings:

        mock_settings.LLM_ADAPTIVE_SOP = False
        mock_settings.LLM_MODEL = "test-model"
        mock_settings.LLM_PROVIDER = "ollama"

        mock_db = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        inc_result = MagicMock()
        inc_result.scalar_one_or_none.return_value = incident
        sop_result = MagicMock()
        sop_result.scalar_one_or_none.return_value = sop
        mock_db.execute = AsyncMock(side_effect=[inc_result, sop_result])
        mock_db.commit = AsyncMock()

        result = await agent.execute(INC_ID, ORG_ID, SOP_ID)

    mock_audit.assert_awaited_once()
    audit_call = mock_audit.call_args.kwargs
    assert audit_call["event_type"] == "sop.llm_agent_completed"
    assert audit_call["detail"]["success"] is True
    assert audit_call["detail"]["iterations"] == 2
    assert len(audit_call["detail"]["tool_calls"]) == 1
    assert audit_call["detail"]["tool_calls"][0]["tool"] == "get_patient_info"


async def test_llm_agent_raises_on_missing_incident():
    agent = _make_agent()

    with patch("app.agent.llm_agent.AsyncSessionLocal") as mock_session, \
         patch("app.agent.llm_agent.settings") as mock_settings:

        mock_settings.LLM_ADAPTIVE_SOP = False
        mock_settings.LLM_MODEL = "test-model"
        mock_settings.LLM_PROVIDER = "ollama"

        mock_db = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        inc_result = MagicMock()
        inc_result.scalar_one_or_none.return_value = None  # incident not found
        mock_db.execute = AsyncMock(return_value=inc_result)

        with pytest.raises(ValueError, match="not found"):
            await agent.execute(INC_ID, ORG_ID, SOP_ID)


async def test_llm_agent_raises_on_missing_sop():
    agent = _make_agent()
    incident = _make_mock_incident()

    with patch("app.agent.llm_agent.AsyncSessionLocal") as mock_session, \
         patch("app.agent.llm_agent.settings") as mock_settings:

        mock_settings.LLM_ADAPTIVE_SOP = False
        mock_settings.LLM_MODEL = "test-model"
        mock_settings.LLM_PROVIDER = "ollama"

        mock_db = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        inc_result = MagicMock()
        inc_result.scalar_one_or_none.return_value = incident
        sop_result = MagicMock()
        sop_result.scalar_one_or_none.return_value = None  # SOP not found
        mock_db.execute = AsyncMock(side_effect=[inc_result, sop_result])
        mock_db.commit = AsyncMock()

        with pytest.raises(ValueError, match="not found"):
            await agent.execute(INC_ID, ORG_ID, SOP_ID)
