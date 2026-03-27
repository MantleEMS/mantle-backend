from typing import Optional
from pydantic import BaseModel, Field


class SOPComplianceRequest(BaseModel):
    sop_text: str = Field(..., description="Full text of the organization's SOP to be analyzed")
    industry_context: Optional[str] = Field(
        default="general workplace",
        description="Industry or context (e.g. 'home healthcare', 'construction', 'manufacturing')",
    )
    osha_standards: Optional[list[str]] = Field(
        default=None,
        description="Specific OSHA standards to check against (e.g. ['29 CFR 1910.1030']). "
                    "If omitted, the LLM selects applicable standards based on the SOP content.",
    )


class ComplianceVariance(BaseModel):
    osha_reference: str = Field(..., description="OSHA standard or section (e.g. '29 CFR 1910.132(d)(1)')")
    requirement: str = Field(..., description="What OSHA requires")
    current_state: str = Field(..., description="What the SOP currently says or omits")
    gap: str = Field(..., description="Description of the gap between requirement and current state")
    severity: str = Field(..., description="critical | major | minor")


class ComplianceRecommendation(BaseModel):
    action: str = Field(..., description="Specific corrective action to take")
    priority: str = Field(..., description="high | medium | low")
    rationale: str = Field(..., description="Why this action is needed")
    suggested_language: Optional[str] = Field(
        default=None,
        description="Optional suggested SOP language to add or replace",
    )


class SOPComplianceResponse(BaseModel):
    compliance_score: int = Field(..., description="Estimated compliance score 0–100")
    summary: str = Field(..., description="High-level summary of the compliance analysis")
    osha_standards_checked: list[str] = Field(..., description="OSHA standards that were evaluated")
    variances: list[ComplianceVariance]
    recommendations: list[ComplianceRecommendation]
