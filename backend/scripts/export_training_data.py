#!/usr/bin/env python3
"""
Export training samples to OpenAI fine-tuning JSONL format.

Usage (from backend/):
    python scripts/export_training_data.py [--output training_data.jsonl] [--quality good] [--min-iterations 2]

The JSONL format is compatible with:
    - OpenAI fine-tuning API (gpt-4o-mini, gpt-3.5-turbo)
    - Unsloth / Axolotl (with --format openai)
    - Any tool that accepts OpenAI conversation format

Each line is a JSON object: {"messages": [...]}
where messages is the full conversation (system + user + assistant + tool calls + tool results).
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow running from backend/ or project root
sys.path.insert(0, str(Path(__file__).parent.parent))


async def export(output: str, quality: str, min_iterations: int, success_only: bool):
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models import TrainingSample

    async with AsyncSessionLocal() as db:
        q = select(TrainingSample).where(TrainingSample.quality == quality)
        if success_only:
            q = q.where(TrainingSample.success == True)
        if min_iterations > 0:
            q = q.where(TrainingSample.iterations >= min_iterations)
        q = q.order_by(TrainingSample.created_at)

        result = await db.execute(q)
        samples = result.scalars().all()

    count = 0
    skipped = 0
    with open(output, "w") as f:
        for sample in samples:
            messages = sample.conversation
            if not messages:
                skipped += 1
                continue
            # Ensure there's at least one assistant message with content
            has_final = any(
                m.get("role") == "assistant" and m.get("content")
                for m in messages
            )
            if not has_final:
                skipped += 1
                continue
            f.write(json.dumps({"messages": messages}) + "\n")
            count += 1

    print(f"Exported {count} samples to {output} ({skipped} skipped — empty or no final answer)")
    print(f"Quality filter: {quality}, success_only: {success_only}, min_iterations: {min_iterations}")


def main():
    parser = argparse.ArgumentParser(description="Export EMS LLM training data to JSONL")
    parser.add_argument("--output", default="training_data.jsonl", help="Output file path")
    parser.add_argument("--quality", default="good", choices=["good", "rejected"], help="Quality filter")
    parser.add_argument("--min-iterations", type=int, default=0, help="Minimum agent iterations (filter out trivial runs)")
    parser.add_argument("--success-only", action="store_true", default=True, help="Only export successful runs")
    parser.add_argument("--all", dest="success_only", action="store_false", help="Include failed runs")
    args = parser.parse_args()

    asyncio.run(export(args.output, args.quality, args.min_iterations, args.success_only))


if __name__ == "__main__":
    main()
