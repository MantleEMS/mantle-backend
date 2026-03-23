"""Unit tests for agent/system_prompt.py."""

import json
import pytest
from app.agent.system_prompt import build_system_prompt, SYSTEM_PROMPT_TEMPLATE


SAMPLE_SOP = {
    "id": "abc-123",
    "name": "Medical Emergency Response",
    "emergency_type": "medical",
    "steps": [
        {"step": 1, "action": "begin_recording", "auto": True},
        {"step": 2, "action": "alert_commander", "auto": True},
    ],
    "responder_checklist": [],
}


def test_build_system_prompt_contains_sop_json():
    prompt = build_system_prompt(SAMPLE_SOP)
    assert "Medical Emergency Response" in prompt
    assert "begin_recording" in prompt


def test_build_system_prompt_embeds_valid_json():
    prompt = build_system_prompt(SAMPLE_SOP)
    # Find the JSON block in the prompt and verify it parses
    sop_json_str = json.dumps(SAMPLE_SOP, indent=2)
    assert sop_json_str in prompt


def test_build_system_prompt_contains_approval_gate_rule():
    prompt = build_system_prompt(SAMPLE_SOP)
    # Must mention that red-tier actions require create_pending_action
    assert "create_pending_action" in prompt
    assert "dispatch_responder" in prompt


def test_build_system_prompt_contains_role_definition():
    prompt = build_system_prompt(SAMPLE_SOP)
    assert "ASSISTANT" in prompt or "assistant" in prompt


def test_build_system_prompt_contains_911_differentiation_rule():
    prompt = build_system_prompt(SAMPLE_SOP)
    assert "medical" in prompt.lower()
    assert "violence" in prompt.lower() or "workplace_violence" in prompt.lower()


def test_build_system_prompt_with_empty_sop():
    empty_sop = {"steps": [], "responder_checklist": []}
    prompt = build_system_prompt(empty_sop)
    assert isinstance(prompt, str)
    assert len(prompt) > 100  # Still has the template content


# ── Adaptive flag ─────────────────────────────────────────────────────────────

def test_build_system_prompt_adaptive_false_by_default():
    prompt = build_system_prompt(SAMPLE_SOP)
    assert "propose_step_adaptation" not in prompt
    assert "Adaptive SOP" not in prompt


def test_build_system_prompt_adaptive_true_includes_section():
    prompt = build_system_prompt(SAMPLE_SOP, adaptive=True)
    assert "Adaptive SOP" in prompt
    assert "propose_step_adaptation" in prompt
    assert "propose_sop_switch" in prompt


def test_build_system_prompt_adaptive_true_includes_guardrails():
    prompt = build_system_prompt(SAMPLE_SOP, adaptive=True)
    # Max proposals guardrail
    assert "2 adaptations" in prompt
    # Must cite evidence rule
    assert "cite" in prompt.lower()


def test_build_system_prompt_adaptive_true_still_has_base_content():
    prompt = build_system_prompt(SAMPLE_SOP, adaptive=True)
    # Base content still present
    assert "create_pending_action" in prompt
    assert "Medical Emergency Response" in prompt


def test_build_system_prompt_adaptive_false_explicit():
    prompt = build_system_prompt(SAMPLE_SOP, adaptive=False)
    assert "propose_step_adaptation" not in prompt
