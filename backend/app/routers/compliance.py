import logging

from fastapi import APIRouter, HTTPException, status

from app.schemas.compliance import SOPComplianceRequest, SOPComplianceResponse
from app.services.compliance_service import analyze_sop_compliance

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["compliance"])


@router.post(
    "/compliance/sop-check",
    response_model=SOPComplianceResponse,
    summary="Analyze SOP against OSHA compliance requirements",
    description=(
        "Public endpoint. Submits an organization's SOP text to an LLM for analysis against "
        "OSHA regulations. Returns identified variances, a compliance score, and actionable "
        "recommendations. No authentication required."
    ),
)
async def check_sop_compliance(body: SOPComplianceRequest) -> SOPComplianceResponse:
    if not body.sop_text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="sop_text must not be empty.",
        )

    try:
        return await analyze_sop_compliance(body)
    except RuntimeError as exc:
        # Provider not configured / package not installed
        logger.error(f"compliance router config error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    except ValueError as exc:
        # LLM returned unparseable output
        logger.error(f"compliance router parse error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM returned an unexpected response format. Please retry.",
        )
    except Exception as exc:
        logger.exception(f"compliance router unexpected error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during compliance analysis.",
        )
