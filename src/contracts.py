from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    source: str = Field(description="Tool/data source name")
    finding: str = Field(description="Concise factual finding")
    reference: str | None = Field(default=None, description="Optional record ID / policy section / endpoint")


class RunTelemetry(BaseModel):
    llm_calls: int | None = None
    tool_calls: int | None = None
    tool_names: list[str] = Field(default_factory=list)
    # Optional extras (all additive; the evaluation contract above is unchanged)
    architecture: str | None = None
    latency_ms: float | None = None
    llm_latency_ms: float | None = None
    retry_wait_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    failed_tools: list[str] = Field(default_factory=list)
    ungrounded_items_dropped: int | None = None
    llm_error: str | None = None
    replayed: bool | None = None  # LLM responses came from a recorded run


class ProcurementDecision(BaseModel):
    request_id: str
    recommendation: str = Field(description="Short recommendation label or sentence")
    evidence: list[EvidenceItem] = Field(default_factory=list)
    required_approvals: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    next_step: str
    human_review_required: bool = True
    telemetry: RunTelemetry | None = None
    # Optional machine-readable action: proceed_to_approval | route_for_review |
    # request_clarification | reuse_existing_tool
    recommended_action: str | None = None


# "rules_only" is a no-LLM reference baseline used by the evaluation.
Architecture = Literal["single", "staged", "rules_only"]

# Suggested approval names for consistency in evaluation:
# Manager, Department Head, Procurement, Finance, CFO, Security, Privacy, Legal
#
# Suggested risk-flag taxonomy (you may add others):
# existing_tool_overlap
# budget_insufficient
# security_review_required
# privacy_review_required
# legal_review_required
# vendor_review_expired
# conflicting_vendor_evidence
# vendor_risk_unavailable
# prompt_injection_detected
# missing_information
