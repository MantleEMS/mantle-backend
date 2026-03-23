"""
LLM quality evaluation harness.
Runs both demo scenarios against the target model and scores the results.

Usage:
  # Test with Ollama (default)
  AI_MODE=llm LLM_PROVIDER=ollama LLM_MODEL=qwen2.5:14b python evaluation/run_eval.py

  # Test with Claude
  AI_MODE=llm LLM_PROVIDER=anthropic LLM_MODEL=claude-sonnet-4-20250514 \\
  ANTHROPIC_API_KEY=sk-ant-... python evaluation/run_eval.py

  # Specify number of runs
  python evaluation/run_eval.py --runs 5

  # Test specific scenario only
  python evaluation/run_eval.py --scenario S1 --runs 10
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure the backend directory is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, delete

from app.database import AsyncSessionLocal, create_tables
from app.models import Incident, Action, Message, Participant, AuditLog, User, Facility, SOP
from app.agent.llm_client import LLMClient, LLMConfig
from app.agent.llm_agent import LLMAgent
from app.tools.registry import build_registry
from app.config import settings
from evaluation.scenarios import SCENARIOS, SCENARIO_BY_ID, Scenario
from evaluation.scoring import score_trace, EvalReport


async def reset_incident_data(db, incident_id):
    """Remove all data associated with a test incident."""
    await db.execute(delete(AuditLog).where(AuditLog.incident_id == incident_id))
    await db.execute(delete(Message).where(Message.incident_id == incident_id))
    await db.execute(delete(Action).where(Action.incident_id == incident_id))
    await db.execute(delete(Participant).where(Participant.incident_id == incident_id))
    await db.execute(delete(Incident).where(Incident.id == incident_id))
    await db.commit()


async def create_test_incident(db, scenario: Scenario, org_id, user_id, facility_id, sop_id):
    """Create a fresh incident for the given scenario."""
    from app.services.incident_service import create_incident
    incident = await create_incident(
        db=db,
        org_id=org_id,
        initiated_by=user_id,
        emergency_type=scenario.emergency_type,
        trigger_source=scenario.trigger_source,
        facility_id=facility_id,
    )
    # Override sop_id to ensure the right SOP is used
    incident.sop_id = sop_id
    await db.commit()
    await db.refresh(incident)
    return incident


async def count_ai_messages(incident_id) -> int:
    """Count messages posted by the AI agent during this run."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Message).where(
                Message.incident_id == incident_id,
                Message.sender_type == "ai",
            )
        )
        return len(result.scalars().all())


async def run_single(
    scenario: Scenario,
    run_number: int,
    llm_agent: LLMAgent,
    org_id,
    user_id,
    facility_id,
    sop_id,
) -> tuple[list[dict], int]:
    """Run a single scenario evaluation. Returns (trace, message_count)."""
    async with AsyncSessionLocal() as db:
        incident = await create_test_incident(db, scenario, org_id, user_id, facility_id, sop_id)
        incident_id = incident.id

    print(f"  Run {run_number}: incident {incident_id} ({scenario.emergency_type})")

    try:
        result = await asyncio.wait_for(
            llm_agent.execute(incident_id, org_id, sop_id),
            timeout=settings.LLM_TIMEOUT + 30,
        )
        trace = result.trace
    except Exception as e:
        print(f"    ERROR: {e}")
        trace = []

    message_count = await count_ai_messages(incident_id)
    return trace, message_count


async def run_evaluation(
    scenarios: list[Scenario],
    runs_per_scenario: int,
    provider: str,
    model: str,
) -> list[EvalReport]:
    await create_tables()

    # Find seed org/user/facility for tests
    async with AsyncSessionLocal() as db:
        org_result = await db.execute(select(User).limit(1))
        sample_user = org_result.scalar_one_or_none()
        if not sample_user:
            print("ERROR: No users found in database. Run seed first: python app/seed/seed_data.py")
            return []

        org_id = sample_user.org_id

        facility_result = await db.execute(
            select(Facility).where(Facility.org_id == org_id).limit(1)
        )
        facility = facility_result.scalar_one_or_none()
        if not facility:
            print("ERROR: No facilities found. Run seed first.")
            return []

    config = LLMConfig(
        provider=provider,
        model=model,
        base_url=settings.LLM_BASE_URL,
        api_key=settings.ANTHROPIC_API_KEY,
        aws_region=settings.AWS_REGION,
        temperature=settings.LLM_TEMPERATURE,
        timeout_seconds=settings.LLM_TIMEOUT,
    )
    registry = build_registry()
    client = LLMClient(config)
    agent = LLMAgent(client, registry)

    reports = []

    for scenario in scenarios:
        print(f"\nScenario {scenario.id}: {scenario.name}")
        print(f"  Provider: {provider}  Model: {model}  Runs: {runs_per_scenario}")

        # Find the SOP for this emergency type
        async with AsyncSessionLocal() as db:
            sop_result = await db.execute(
                select(SOP).where(
                    SOP.org_id == org_id,
                    SOP.emergency_type == scenario.emergency_type,
                    SOP.is_active == True,
                ).limit(1)
            )
            sop = sop_result.scalar_one_or_none()
            if not sop:
                print(f"  SKIP: No SOP found for {scenario.emergency_type}")
                continue
            sop_id = sop.id

        report = EvalReport(provider=provider, model=model, scenario_id=scenario.id)

        for run in range(1, runs_per_scenario + 1):
            trace, message_count = await run_single(
                scenario=scenario,
                run_number=run,
                llm_agent=agent,
                org_id=org_id,
                user_id=sample_user.id,
                facility_id=facility.id,
                sop_id=sop_id,
            )
            run_score = score_trace(trace, scenario, message_count, run_number=run)
            report.runs.append(run_score)
            print(
                f"    score={run_score.weighted_score:.2f}  "
                f"gate={run_score.approval_gate_respect:.0f}/5  "
                f"tools={run_score.tool_correctness:.0f}/5  "
                f"msgs={message_count}"
            )

        report.print_summary()
        reports.append(report)

    return reports


def save_reports(reports: list[EvalReport], provider: str, model: str):
    Path("evaluation/reports").mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    safe_model = model.replace("/", "-").replace(":", "-")
    filename = f"evaluation/reports/eval_{provider}_{safe_model}_{ts}.json"

    data = []
    for r in reports:
        data.append({
            "provider": r.provider,
            "model": r.model,
            "scenario_id": r.scenario_id,
            "avg_score": r.avg_score,
            "approval_gate_perfect": r.approval_gate_perfect,
            "reliability": r.reliability,
            "verdict": r.verdict,
            "runs": [
                {
                    "run_number": s.run_number,
                    "weighted_score": s.weighted_score,
                    "tool_correctness": s.tool_correctness,
                    "approval_gate_respect": s.approval_gate_respect,
                    "thread_message_quality": s.thread_message_quality,
                    "sop_differentiation": s.sop_differentiation,
                    "reliability_pass": s.reliability_pass,
                }
                for s in r.runs
            ],
        })

    with open(filename, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Report saved: {filename}")


async def main():
    parser = argparse.ArgumentParser(description="Mantle LLM evaluation harness")
    parser.add_argument("--runs", type=int, default=10, help="Runs per scenario (default: 10)")
    parser.add_argument("--scenario", choices=list(SCENARIO_BY_ID.keys()), help="Run one scenario only")
    args = parser.parse_args()

    provider = settings.LLM_PROVIDER
    model = settings.LLM_MODEL

    if settings.AI_MODE != "llm":
        print("WARNING: AI_MODE is not 'llm'. Set AI_MODE=llm to evaluate the LLM agent.")
        print("Exiting.")
        return

    scenarios = [SCENARIO_BY_ID[args.scenario]] if args.scenario else SCENARIOS
    reports = await run_evaluation(scenarios, args.runs, provider, model)
    if reports:
        save_reports(reports, provider, model)


if __name__ == "__main__":
    asyncio.run(main())
