"""
ThreadAgent — live conversation agent for active incident threads.

Invoked as a background task whenever a human posts a message to an incident thread.
Stateless: loads all context fresh from the database on every invocation.
Uses the SOP as a rulebook (not a script) to constrain its responses.
"""

import json
import logging
import time
from uuid import UUID

from sqlalchemy import select, and_

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Incident, SOP, Message, Action
from app.agent.llm_client import LLMClient, AgentResult
from app.services.thread_service import create_message, write_audit
from app.tools.registry import ToolRegistry
from app.metrics import thread_agent_runs

logger = logging.getLogger(__name__)

# Tools the thread agent is allowed to use (subset of full registry)
THREAD_AGENT_TOOLS = [
    "create_pending_action",
    "post_thread_message",
    "update_incident",
]

THREAD_AGENT_MAX_ITERATIONS = 3


# ── Prompt builder ───────────────────────────────────────────────────────────

def _extract_escalation_rules(sop_steps: list[dict]) -> str:
    """Extract escalation_rules from SOP steps JSONB if present."""
    # Look for escalation_rules at the top level of the steps structure
    # (some SOPs store them as a peer to the step list)
    rules = []
    if isinstance(sop_steps, dict):
        raw = sop_steps.get("escalation_rules", [])
    elif isinstance(sop_steps, list):
        # Steps is a plain list — check if any step has escalation metadata
        raw = []
        for step in sop_steps:
            if isinstance(step, dict) and "escalation_rules" in step:
                raw.extend(step["escalation_rules"])
    else:
        raw = []

    for rule in raw:
        trigger = rule.get("trigger", "unknown")
        actions = ", ".join(rule.get("actions", []))
        applies = rule.get("applies_to")
        line = f"- If {trigger}: {actions}"
        if applies:
            line += f" (applies to: {', '.join(applies)})"
        rules.append(line)
    return "\n".join(rules) if rules else "- No additional escalation rules defined."


def _extract_resolution_conditions(sop_steps: list | dict) -> str:
    """Extract resolution_conditions from SOP JSONB."""
    conditions = []
    raw = []
    if isinstance(sop_steps, dict):
        raw = sop_steps.get("resolution_conditions", [])

    for cond in raw:
        if isinstance(cond, str):
            conditions.append(f"- {cond}")
        elif isinstance(cond, dict):
            # Conditional resolution: {"if": "weapon_reported", "then": "weapon_secured_confirmed"}
            conditions.append(f"- If {cond.get('if', '?')}: require {cond.get('then', '?')}")
    return "\n".join(conditions) if conditions else "- Standard resolution: commander confirms scene clear + worker safe."


def _extract_notification_rules(sop_steps: list | dict) -> str:
    """Extract notification_rules from SOP JSONB."""
    rules = []
    raw = []
    if isinstance(sop_steps, dict):
        raw = sop_steps.get("notification_rules", [])

    for rule in raw:
        if isinstance(rule, dict):
            notify = rule.get("notify", "?")
            when = rule.get("when", "always")
            not_when = rule.get("not_when")
            line = f"- Notify {notify} when: {when}"
            if not_when:
                line += f" (NOT when: {not_when})"
            rules.append(line)
    return "\n".join(rules) if rules else "- Follow default notification rules for this emergency type."


def build_thread_agent_prompt(sop: dict, incident: dict) -> str:
    """
    Build the system prompt for the Thread Agent.

    The SOP is injected as a RULEBOOK — constraints on escalation, resolution,
    notifications, and responder guidance — not as a sequential script.
    """
    steps_data = sop.get("steps", [])
    checklist = sop.get("responder_checklist", [])
    emergency_type = sop.get("emergency_type", incident.get("emergency_type", "unknown"))
    sop_code = sop.get("sop_code", sop.get("name", "Unknown"))

    escalation_rules = _extract_escalation_rules(steps_data)
    resolution_conditions = _extract_resolution_conditions(steps_data)
    notification_rules = _extract_notification_rules(steps_data)

    # Format checklist for display
    if isinstance(checklist, list):
        checklist_text = "\n".join(
            f"  {i+1}. {item}" if isinstance(item, str) else f"  {i+1}. {json.dumps(item)}"
            for i, item in enumerate(checklist)
        )
    else:
        checklist_text = json.dumps(checklist, indent=2)

    # Format steps for reference
    steps_text = json.dumps(steps_data, indent=2) if steps_data else "No steps defined."

    return f"""\
You are Mantle, the AI safety assistant in an active emergency thread.
You are monitoring a live incident conversation and providing real-time guidance.

## Active SOP: {sop_code} — {sop.get('name', '')}
Emergency type: {emergency_type}
Current severity: {incident.get('severity', 3)}
Incident status: {incident.get('status', 'active')}

## SOP Steps (for reference — the SOP Launcher already executed these)
{steps_text}

## Responder Checklist (track completion against these)
{checklist_text}

## Your Constraints From This SOP

ESCALATION RULES:
- This is a {emergency_type} incident.
- If a weapon is reported and this is a workplace_violence SOP: upgrade severity \
to 5, recommend confirming armed law enforcement response.
- If a weapon is reported and this is a medical SOP: recommend the commander switch \
to the workplace violence SOP. Do NOT automatically escalate to armed response.
- If additional victims are reported: recommend additional responder dispatch.
- If fire/smoke/gas is mentioned: recommend fire department regardless of \
original emergency type.
{escalation_rules}

RESOLUTION RULES:
- The incident can only be resolved by the commander.
- You may RECOMMEND resolution when: the responder confirms scene is clear, \
AND the worker confirms they are safe.
- For violence SOPs with a weapon reported: resolution requires confirmation \
that the weapon is secured.
- For medical SOPs: resolution requires EMS handoff OR worker confirmation \
that the patient is stable.
{resolution_conditions}

RESPONDER GUIDANCE:
- When the responder asks for guidance, reference the responder checklist above.
- Do not invent steps that are not in the checklist.
- Track which checklist items the responder has completed based on their messages.
- If they skip a step, note it but do not block them.

NOTIFICATION RULES:
{notification_rules}
- For workplace_violence SOPs: Do NOT notify the patient's emergency contact \
(the aggressor may be a family member).
- For medical SOPs: DO recommend notifying the patient's emergency contact \
if not already done.

## Your Role
- You are an ASSISTANT, not a decision-maker.
- You respond to new messages in the thread with situational awareness.
- Green-tier actions (posting messages, updating incident) you execute directly.
- Red-tier actions (dispatch, 911, notifications) you MUST present as pending \
decisions using create_pending_action.
- Keep thread messages concise, professional, and calming.
- If the message is routine (e.g. "acknowledged", "en route"), you may choose \
not to respond at all.

OUT OF SCOPE — never do these regardless of SOP:
- Never provide medical advice beyond what the SOP specifies.
- Never instruct the worker to confront an aggressor.
- Never recommend the responder enter an unsecured scene with a known weapon.
- Never override a commander decision.
"""


# ── ThreadAgent ──────────────────────────────────────────────────────────────

class ThreadAgent:
    """
    Live conversation agent for incident threads.

    Instantiated once at app startup. Stateless between invocations —
    all context is loaded fresh from the database on every call.
    """

    def __init__(self, llm: LLMClient, registry: ToolRegistry):
        self.llm = llm
        self.registry = registry.get_subset(THREAD_AGENT_TOOLS)

    async def on_message(self, incident_id: UUID, new_message_id: UUID):
        """
        Called as a background task when a human posts to the thread.
        Loads context, calls the LLM, and lets it decide whether/how to respond.
        """
        t0 = time.monotonic()

        try:
            async with AsyncSessionLocal() as db:
                # Load incident
                inc_result = await db.execute(
                    select(Incident).where(Incident.id == incident_id)
                )
                incident = inc_result.scalar_one_or_none()
                if not incident:
                    logger.warning(f"thread_agent: incident {incident_id} not found")
                    return

                # Skip resolved/cancelled incidents
                if incident.status in ("resolved", "cancelled"):
                    logger.info(f"thread_agent: skipping {incident_id} status={incident.status}")
                    return

                # Load SOP
                sop = None
                if incident.sop_id:
                    sop_result = await db.execute(
                        select(SOP).where(SOP.id == incident.sop_id)
                    )
                    sop = sop_result.scalar_one_or_none()

                if not sop:
                    logger.warning(f"thread_agent: no SOP for incident {incident_id}")
                    return

                # Load recent messages (last 30 for context window)
                msg_result = await db.execute(
                    select(Message)
                    .where(Message.incident_id == incident_id)
                    .order_by(Message.seq.desc())
                    .limit(30)
                )
                recent_messages = list(reversed(msg_result.scalars().all()))

                # Load the new message specifically
                new_msg_result = await db.execute(
                    select(Message).where(Message.id == new_message_id)
                )
                new_message = new_msg_result.scalar_one_or_none()

                # Load pending actions
                action_result = await db.execute(
                    select(Action).where(
                        and_(
                            Action.incident_id == incident_id,
                            Action.status == "pending",
                        )
                    )
                )
                pending_actions = action_result.scalars().all()

                # Build SOP dict
                sop_dict = {
                    "id": str(sop.id),
                    "name": sop.name,
                    "sop_code": sop.sop_code,
                    "emergency_type": sop.emergency_type,
                    "steps": sop.steps or [],
                    "responder_checklist": sop.responder_checklist or [],
                }

                incident_dict = {
                    "id": str(incident.id),
                    "status": incident.status,
                    "emergency_type": incident.emergency_type,
                    "severity": incident.severity,
                }

            # Build system prompt
            system_prompt = build_thread_agent_prompt(sop_dict, incident_dict)

            # Format conversation history as LLM messages
            messages = self._format_thread_as_conversation(
                recent_messages, pending_actions, new_message
            )

            logger.info(
                f"thread_agent [{incident_id}] starting "
                f"emergency={incident.emergency_type} sop={sop.sop_code} "
                f"msgs={len(recent_messages)} pending_actions={len(pending_actions)}"
            )

            # Run the agent loop (max 3 iterations)
            result = await self.llm.run_agent(
                system_prompt=system_prompt,
                messages=messages,
                registry=self.registry,
                max_iterations=THREAD_AGENT_MAX_ITERATIONS,
            )

            # If the LLM returned text but never called post_thread_message,
            # post the response to the thread so participants can see it.
            already_posted = any(
                t.get("tool") == "post_thread_message" for t in result.trace
            )
            if result.final_text and not already_posted:
                await self._post_final_text(incident_id, result.final_text)

            elapsed = time.monotonic() - t0
            thread_agent_runs.labels(status="success").inc()
            logger.info(
                f"thread_agent [{incident_id}] finished "
                f"success={result.success} iterations={result.iterations} "
                f"tool_calls={len(result.trace)} elapsed={elapsed:.1f}s"
            )

            # Log trace for evaluation
            await self._log_trace(incident_id, incident.org_id, result)

        except Exception:
            elapsed = time.monotonic() - t0
            thread_agent_runs.labels(status="error").inc()
            logger.exception(
                f"thread_agent [{incident_id}] failed elapsed={elapsed:.1f}s"
            )

    def _format_thread_as_conversation(
        self,
        recent_messages: list[Message],
        pending_actions: list[Action],
        new_message: Message | None,
    ) -> list[dict]:
        """
        Convert thread messages into the LLM conversation format.

        Human messages → user role, AI messages → assistant role.
        System messages and pending actions are folded into user context.
        """
        # Build a context block with pending actions and the latest message
        context_parts = []

        if pending_actions:
            actions_summary = "\n".join(
                f"- [{a.tier}] {a.action_type}: {a.description} (status: {a.status})"
                for a in pending_actions
            )
            context_parts.append(f"PENDING ACTIONS:\n{actions_summary}")

        # Format message history as a readable thread
        thread_lines = []
        for msg in recent_messages:
            sender = msg.sender_type.upper()
            ts = msg.created_at.strftime("%H:%M:%S") if msg.created_at else "?"
            thread_lines.append(f"[{ts}] {sender}: {msg.content}")

            # Note attachments
            meta = msg.meta or {}
            if meta.get("attachment_type") == "photo":
                thread_lines.append(f"  (attached photo: {meta.get('attachment_url', 'url not available')})")

        context_parts.append(f"THREAD HISTORY:\n" + "\n".join(thread_lines))

        if new_message:
            context_parts.append(
                f"NEW MESSAGE (respond to this):\n"
                f"From: {new_message.sender_type}\n"
                f"Content: {new_message.content}"
            )

        user_content = "\n\n".join(context_parts)
        return [{"role": "user", "content": user_content}]

    async def _post_final_text(self, incident_id: UUID, text: str):
        """Post the LLM's text response to the thread so all participants see it."""
        try:
            async with AsyncSessionLocal() as db:
                await create_message(
                    db=db,
                    incident_id=incident_id,
                    sender_type="ai",
                    message_type="text",
                    content=text,
                )
            logger.info(f"thread_agent [{incident_id}] posted final_text to thread")
        except Exception:
            logger.exception(f"thread_agent [{incident_id}] failed to post final_text")

    async def _log_trace(self, incident_id: UUID, org_id: UUID, result: AgentResult):
        """Write agent trace to audit log for evaluation and debugging."""
        async with AsyncSessionLocal() as db:
            await write_audit(
                db=db,
                org_id=org_id,
                event_type="thread_agent.completed",
                actor_type="ai",
                incident_id=incident_id,
                detail={
                    "success": result.success,
                    "iterations": result.iterations,
                    "tool_calls": [
                        {"tool": t["tool"], "params": t["params"]}
                        for t in result.trace
                    ],
                    "final_text": result.final_text[:500] if result.final_text else None,
                },
            )
