"""Procurement Copilot orchestrator.

Pipeline (same for every architecture):
  1. tools      - code gathers all evidence (src/tools.py)
  2. guard      - code scans untrusted text for injection; flags, never stops (src/guard.py)
  3. rules      - code applies deterministic policy -> decision floor (src/rules.py)
  4. agent(s)   - LLM judgment: single agent (A) or analyst -> reviewer (B) (src/agents.py)
  5. merge      - code unions LLM additions onto the floor, enforces action guardrails,
                  forces human review; if the LLM fails, a rules-only decision is returned.

Architectures differ ONLY in step 4, so the evaluation isolates the LLM topology.
"""
from __future__ import annotations

import re
import time

from src.agents import LLMAssessment, run_single_agent, run_staged_agents
from src.contracts import Architecture, EvidenceItem, ProcurementDecision, RunTelemetry
from src.data_access import get_request, load_policy_text
from src.guard import scan_request_and_evidence
from src.llm import LLMError
from src.rules import ALLOWED_FLAGS, APPROVAL_ORDER, RulesOutcome, evaluate_rules, sort_approvals
from src.telemetry import RunTelemetryCounter
from src.tools import ToolResult, gather_evidence

ACTION_LABELS = {
    "proceed_to_approval": "Proceed to business approval",
    "route_for_review": "Route for specialist review",
    "request_clarification": "Request clarification",
    "reuse_existing_tool": "Reuse existing tool",
}
SPECIALIST_FLAGS = {
    "budget_insufficient", "security_review_required", "privacy_review_required", "legal_review_required",
    "vendor_risk_unavailable", "conflicting_vendor_evidence", "vendor_review_expired",
}
_POLICY_REF = re.compile(r"^Policy §\d+$")


# ---------------------------------------------------------------------------
# Steps 1-3: deterministic
# ---------------------------------------------------------------------------
def run_deterministic(request: dict, telemetry: RunTelemetryCounter) -> tuple[dict[str, ToolResult], RulesOutcome]:
    tools = gather_evidence(request)
    for result in tools.values():
        telemetry.record_tool_call(result.tool, ok=result.ok)
    injection_hits = scan_request_and_evidence(request, tools)
    rules = evaluate_rules(request, tools, injection_hits)
    return tools, rules


def known_references(tools: dict[str, ToolResult]) -> set[str]:
    refs: set[str] = set()
    for r in tools.values():
        refs.update(r.references)
    return refs


def _grounded(ref: str, refs: set[str]) -> bool:
    ref = (ref or "").strip()
    return ref in refs or bool(_POLICY_REF.match(ref))


# ---------------------------------------------------------------------------
# Step 5: merge (code has the final say)
# ---------------------------------------------------------------------------
def merge_decision(request_id: str, request: dict, tools: dict[str, ToolResult], rules: RulesOutcome,
                   assessment: LLMAssessment | None, telemetry: RunTelemetryCounter, architecture: str,
                   llm_error: str | None = None) -> ProcurementDecision:
    approvals = list(rules.approvals)
    flags = list(rules.flags)
    evidence = [EvidenceItem(**e) for e in rules.evidence]
    dropped = 0

    if assessment is None:
        action = rules.default_action
        if llm_error:
            flags.append("llm_unavailable")
            evidence.append(EvidenceItem(source="pipeline", finding=f"AI assessment unavailable ({llm_error}); decision is based on deterministic rules only.", reference=None))
        recommendation = _fallback_recommendation(action, rules)
        next_step = _fallback_next_step(action, approvals, rules)
    else:
        refs = known_references(tools)
        for item in assessment.additional_approvals:
            name = next((a for a in APPROVAL_ORDER if a.lower() == item.name.strip().lower()), None)
            if name and name not in approvals:
                approvals.append(name)
                evidence.append(EvidenceItem(source="ai_assessment", finding=f"Added {name} approval: {item.reason}", reference=None))
        for item in assessment.additional_risk_flags:
            flag = item.name.strip().lower().replace(" ", "_")
            if flag in ALLOWED_FLAGS and flag not in flags:
                flags.append(flag)
                evidence.append(EvidenceItem(source="ai_assessment", finding=f"Added flag {flag}: {item.reason}", reference=None))
        if assessment.injection_suspected and "prompt_injection_detected" not in flags:
            flags.append("prompt_injection_detected")
            evidence.append(EvidenceItem(source="ai_assessment", finding="Model flagged instruction-like content in business data; it was ignored.", reference="Policy §9"))
        if assessment.overlap_assessment:
            evidence.append(EvidenceItem(source="ai_assessment", finding=f"Overlap assessment: {assessment.overlap_assessment}", reference="Policy §3"))
        for kp in assessment.key_points:
            if _grounded(kp.reference, refs):
                evidence.append(EvidenceItem(source="ai_assessment", finding=kp.finding, reference=kp.reference.strip()))
            else:
                dropped += 1  # ungrounded claims never reach the user
        action = assessment.recommended_action
        recommendation = assessment.recommendation
        next_step = assessment.next_step

    # Guardrails on the action: the model cannot downgrade what code has established.
    if rules.missing_information and action != "request_clarification":
        evidence.append(EvidenceItem(source="pipeline", finding=f"Model action '{action}' overridden: material information is missing.", reference="Policy §1"))
        action = "request_clarification"
        next_step = _fallback_next_step(action, approvals, rules) + " " + next_step
    else:
        if action == "reuse_existing_tool" and "existing_tool_overlap" not in rules.flags:
            # Reuse needs an overlapping product; the deterministic catalog check found none.
            evidence.append(EvidenceItem(source="pipeline", finding=f"Model suggested reusing an existing tool, but the catalog check found no overlapping product; overridden. Model's text: {recommendation}", reference="Policy §3"))
            action = rules.default_action
            recommendation = _fallback_recommendation(action, rules)
            next_step = _fallback_next_step(action, approvals, rules)
        if action == "proceed_to_approval" and SPECIALIST_FLAGS.intersection(flags):
            evidence.append(EvidenceItem(source="pipeline", finding="Model action 'proceed_to_approval' overridden: specialist review or unresolved risk flags are present.", reference=None))
            action = "route_for_review"

    approvals = sort_approvals(approvals)
    t = RunTelemetry(
        llm_calls=telemetry.llm_calls,
        tool_calls=telemetry.tool_calls,
        tool_names=telemetry.tool_names,
        architecture=architecture,
        llm_latency_ms=round(telemetry.llm_latency_ms, 1),
        retry_wait_ms=round(telemetry.retry_wait_ms, 1),
        prompt_tokens=telemetry.prompt_tokens,
        completion_tokens=telemetry.completion_tokens,
        failed_tools=telemetry.failed_tools,
        ungrounded_items_dropped=dropped,
        llm_error=llm_error,
    )
    return ProcurementDecision(
        request_id=request_id,
        recommendation=f"{ACTION_LABELS[action]}: {recommendation}",
        evidence=evidence,
        required_approvals=approvals,
        missing_information=list(rules.missing_information),
        risk_flags=flags,
        next_step=next_step,
        human_review_required=True,  # always: the copilot only recommends (policy §11)
        telemetry=t,
        recommended_action=action,
    )


REVIEW_PHRASES = {
    "budget_insufficient": "a Finance budget exception",
    "security_review_required": "Security review",
    "privacy_review_required": "Privacy review",
    "legal_review_required": "Legal review",
    "conflicting_vendor_evidence": "resolution of conflicting vendor records",
    "vendor_risk_unavailable": "manual verification of vendor risk (service unavailable)",
    "existing_tool_overlap": "confirmation that existing tools do not already cover the need",
}


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _fallback_recommendation(action: str, rules: RulesOutcome) -> str:
    if action == "request_clarification":
        return "The request is incomplete; it cannot be assessed for approval until the missing information is provided."
    if action == "route_for_review":
        needs = [REVIEW_PHRASES[f] for f in rules.flags if f in REVIEW_PHRASES]
        return f"Needs {_join(needs)} before any approval." if needs else "Needs human review before any approval."
    return "Deterministic checks found no blocking issues; business approval is still required."


def _fallback_next_step(action: str, approvals: list[str], rules: RulesOutcome) -> str:
    if action == "request_clarification":
        return "Ask the requester for: " + "; ".join(rules.missing_information) + "."
    return "Send the evidence pack to: " + ", ".join(approvals) + " for human review and approval." if approvals else "Send to a procurement analyst for manual review."


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def process_request_with_trace(request_id: str, request: dict, architecture: Architecture = "single") -> tuple[ProcurementDecision, dict]:
    """Run the pipeline and also return intermediate artefacts (for the UI evidence panel)."""
    if architecture not in ("single", "staged", "rules_only"):
        raise ValueError(f"Unknown architecture: {architecture}")
    start = time.perf_counter()
    telemetry = RunTelemetryCounter()
    tools, rules = run_deterministic(request, telemetry)

    assessment, pack, llm_error = None, None, None
    if architecture != "rules_only":
        policy = load_policy_text()
        try:
            if architecture == "single":
                assessment = run_single_agent(request, tools, rules, policy, telemetry)
            else:
                assessment, pack = run_staged_agents(request, tools, rules, policy, telemetry)
        except LLMError as exc:
            llm_error = str(exc)

    decision = merge_decision(request_id, request, tools, rules, assessment, telemetry, architecture, llm_error)
    decision.telemetry.latency_ms = round((time.perf_counter() - start) * 1000 + telemetry.replayed_ms, 1)
    if telemetry.replayed_ms:
        decision.telemetry.replayed = True
    trace = {
        "tools": {k: v.to_dict() for k, v in tools.items()},
        "rules": rules.to_dict(),
        "assessment": assessment.model_dump() if assessment else None,
        "analyst_pack": pack.model_dump() if pack else None,
    }
    return decision, trace


def process_request(request_id: str, request: dict, architecture: Architecture = "single") -> ProcurementDecision:
    return process_request_with_trace(request_id, request, architecture)[0]


def handle_request(request_id: str, architecture: Architecture = "single") -> ProcurementDecision:
    """Assessment adapter (signature unchanged)."""
    return process_request(request_id, get_request(request_id), architecture)
