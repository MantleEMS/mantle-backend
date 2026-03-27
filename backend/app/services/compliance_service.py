"""
compliance_service.py — OSHA SOP compliance analysis using an LLM.

Supports the same providers as the rest of the system (Anthropic, Bedrock, Ollama).
This is a single-shot (non-agentic) call — no tool use, just structured analysis.
"""

import json
import logging
from typing import Optional

from app.config import settings
from app.schemas.compliance import (
    ComplianceRecommendation,
    ComplianceVariance,
    SOPComplianceRequest,
    SOPComplianceResponse,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an expert OSHA compliance analyst specializing in workplace safety regulations.
Your task is to analyze an organization's Standard Operating Procedure (SOP) against \
applicable OSHA requirements and identify compliance gaps.

When given an SOP text and optional context/standards, you must:
1. Identify which OSHA standards apply (use the provided list if given, otherwise determine them from content).
2. Find every gap where the SOP is missing a required element, is ambiguous, or contradicts OSHA rules.
3. Assign severity: "critical" (violation that could cause serious harm or immediate citation), \
"major" (likely citation during inspection), or "minor" (documentation/best-practice gap).
4. Provide concrete, actionable recommendations with optional suggested SOP language.
5. Estimate an overall compliance score 0–100 (100 = fully compliant).

You MUST respond with a single valid JSON object matching this exact schema — no markdown, \
no commentary outside the JSON:

{
  "compliance_score": <integer 0-100>,
  "summary": "<one-paragraph executive summary>",
  "osha_standards_checked": ["<standard1>", ...],
  "variances": [
    {
      "osha_reference": "<e.g. 29 CFR 1910.132(d)(1)>",
      "requirement": "<what OSHA requires>",
      "current_state": "<what the SOP says or omits>",
      "gap": "<description of the gap>",
      "severity": "<critical|major|minor>"
    }
  ],
  "recommendations": [
    {
      "action": "<specific corrective action>",
      "priority": "<high|medium|low>",
      "rationale": "<why this is needed>",
      "suggested_language": "<optional draft SOP language or null>"
    }
  ]
}
"""


def _build_user_message(request: SOPComplianceRequest) -> str:
    parts = [f"Industry context: {request.industry_context}"]
    if request.osha_standards:
        parts.append(f"OSHA standards to check: {', '.join(request.osha_standards)}")
    parts.append(f"\nSOP TEXT:\n{request.sop_text}")
    return "\n".join(parts)


def _parse_response(raw: str) -> dict:
    """Extract JSON from the LLM response, stripping any surrounding markdown fences."""
    text = raw.strip()
    # Strip ```json ... ``` fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text)


def _build_response(data: dict) -> SOPComplianceResponse:
    variances = [ComplianceVariance(**v) for v in data.get("variances", [])]
    recommendations = [ComplianceRecommendation(**r) for r in data.get("recommendations", [])]
    return SOPComplianceResponse(
        compliance_score=int(data["compliance_score"]),
        summary=data["summary"],
        osha_standards_checked=data.get("osha_standards_checked", []),
        variances=variances,
        recommendations=recommendations,
    )


# ── Provider-specific callers ──────────────────────────────────────────────────

async def _call_anthropic(user_message: str) -> str:
    try:
        import anthropic as anthropic_sdk
    except ImportError:
        raise RuntimeError("anthropic package not installed. Run: pip install anthropic")

    if settings.LLM_PROVIDER == "bedrock":
        client = anthropic_sdk.AsyncAnthropicBedrock(aws_region=settings.AWS_REGION)
    else:
        if not settings.ANTHROPIC_API_KEY:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Configure it in .env or environment."
            )
        client = anthropic_sdk.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    model = settings.LLM_MODEL
    response = await client.messages.create(
        model=model,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
        max_tokens=settings.LLM_MAX_TOKENS,
        temperature=settings.LLM_TEMPERATURE,
    )
    return " ".join(b.text for b in response.content if b.type == "text")


async def _call_openai_compatible(user_message: str) -> str:
    try:
        import openai as openai_sdk
    except ImportError:
        raise RuntimeError("openai package not installed. Run: pip install openai")

    client = openai_sdk.AsyncOpenAI(
        base_url=f"{settings.LLM_BASE_URL.rstrip('/')}/v1",
        api_key="ollama",
    )
    response = await client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=settings.LLM_TEMPERATURE,
        max_tokens=settings.LLM_MAX_TOKENS,
        extra_body={"options": {"num_ctx": settings.LLM_NUM_CTX}},
    )
    return response.choices[0].message.content or ""


# ── Public entry point ─────────────────────────────────────────────────────────

async def analyze_sop_compliance(request: SOPComplianceRequest) -> SOPComplianceResponse:
    """
    Send the SOP text to the configured LLM and return a structured OSHA compliance analysis.
    """
    user_message = _build_user_message(request)
    provider = settings.LLM_PROVIDER

    logger.info(
        f"compliance_service provider={provider} model={settings.LLM_MODEL} "
        f"sop_chars={len(request.sop_text)}"
    )

    if provider in ("anthropic", "bedrock"):
        raw = await _call_anthropic(user_message)
    else:
        raw = await _call_openai_compatible(user_message)

    logger.info(f"compliance_service raw_response_chars={len(raw)}")

    try:
        data = _parse_response(raw)
        result = _build_response(data)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.error(f"compliance_service failed to parse LLM response: {exc}\nraw={raw[:500]!r}")
        raise ValueError(f"LLM returned an unparseable response: {exc}") from exc

    logger.info(
        f"compliance_service done score={result.compliance_score} "
        f"variances={len(result.variances)} recommendations={len(result.recommendations)}"
    )
    return result
