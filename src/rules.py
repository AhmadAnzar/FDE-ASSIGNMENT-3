"""Deterministic policy rules engine.

Turns tool results into the *floor* of every decision: required approvals,
risk flags, missing information and tool-grounded evidence items, each tied to
a policy section. The LLM may add to this floor but can never remove from it
(see src/pipeline.py::merge_decision).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from src.tools import ToolResult

REFERENCE_DATE = date(2026, 9, 30)  # policy data snapshot date
REVIEW_VALID_DAYS = 365
LEGAL_NEW_VENDOR_THRESHOLD = 10_000

APPROVAL_ORDER = ["Manager", "Department Head", "Finance", "CFO", "Procurement", "Security", "Privacy", "Legal"]
ALLOWED_FLAGS = [
    "existing_tool_overlap", "budget_insufficient", "security_review_required", "privacy_review_required",
    "legal_review_required", "vendor_review_expired", "conflicting_vendor_evidence", "vendor_risk_unavailable",
    "prompt_injection_detected", "missing_information", "llm_unavailable",
]

# Keywords in data_access_level / integrations that trigger Security review (policy §5)
SECURITY_DATA_KEYWORDS = ["source", "production", "cloud", "confidential", "pii", "personal", "credential", "secret"]
SECURITY_INTEGRATION_KEYWORDS = ["production", "cloud account", "git", "source", "credential", "secret"]
PII_KEYWORDS = ["pii", "personal"]
UNKNOWN_VALUES = {"", "unknown", "none_specified", "tbd", "n/a"}

ACTIONS = ["proceed_to_approval", "route_for_review", "request_clarification", "reuse_existing_tool"]


@dataclass
class RulesOutcome:
    approvals: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    reasons: list[dict] = field(default_factory=list)  # {"rule", "finding", "policy"}
    default_action: str = "route_for_review"
    facts: dict = field(default_factory=dict)

    def add_approval(self, name: str) -> None:
        if name not in self.approvals:
            self.approvals.append(name)

    def add_flag(self, flag: str) -> None:
        if flag not in self.flags:
            self.flags.append(flag)

    def add_reason(self, rule: str, finding: str, policy: str) -> None:
        self.reasons.append({"rule": rule, "finding": finding, "policy": policy})
        self.evidence.append({"source": "policy_rules", "finding": finding, "reference": policy})

    def to_dict(self) -> dict:
        return {
            "required_approvals": self.approvals,
            "risk_flags": self.flags,
            "missing_information": self.missing_information,
            "rule_findings": self.reasons,
            "default_action": self.default_action,
            "facts": self.facts,
        }


def sort_approvals(approvals: list[str]) -> list[str]:
    known = [a for a in APPROVAL_ORDER if a in approvals]
    return known + [a for a in approvals if a not in APPROVAL_ORDER]


def _norm(value) -> str:
    return str(value or "").strip().lower()


def _parse_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def review_age_days(value) -> int | None:
    d = _parse_date(value)
    return (REFERENCE_DATE - d).days if d else None


def is_review_current(value) -> bool:
    age = review_age_days(value)
    return age is not None and 0 <= age <= REVIEW_VALID_DAYS


def _money(x: float) -> str:
    return f"${x:,.0f}" if float(x).is_integer() else f"${x:,.2f}"


# ---------------------------------------------------------------------------
# Evidence items straight from tool output (no model involved)
# ---------------------------------------------------------------------------
def _tool_evidence(request: dict, tools: dict[str, ToolResult]) -> list[dict]:
    ev: list[dict] = []

    emp = tools["employee"]
    if emp.ok and emp.data:
        d = emp.data
        ev.append({"source": "lookup_employee", "finding": f"Requester {d['employee_id']} ({d['name']}) is {d['level']} in {d['department']}.", "reference": d["employee_id"]})
    else:
        ev.append({"source": "lookup_employee", "finding": f"Requester '{request.get('requester_id')}' not found in employee records.", "reference": None})

    b = tools["budget"]
    if b.ok and b.data and b.data.get("status") in ("sufficient", "insufficient"):
        d = b.data
        verdict = "within available budget" if d["status"] == "sufficient" else f"exceeds available budget by {_money(d['shortfall_usd'])}"
        ev.append({"source": "check_budget", "finding": f"Requested {_money(d['requested_usd'])} vs {_money(d['available_usd'])} available in {d['department']} software budget: {verdict}.", "reference": b.references[0]})
    else:
        reason = (b.data or {}).get("reason") or (b.data or {}).get("status") or b.error
        ev.append({"source": "check_budget", "finding": f"Budget could not be checked ({reason}).", "reference": None})

    t = tools["thresholds"].data
    if t["tier"] == "undetermined":
        ev.append({"source": "approval_thresholds", "finding": "Financial approval tier cannot be determined: annual cost is missing.", "reference": "Policy §4"})
    else:
        ev.append({"source": "approval_thresholds", "finding": f"Annual amount tier {t['tier']} requires: {', '.join(t['minimum_approvals'])}.", "reference": "Policy §4"})

    c = tools["catalog"]
    if c.ok:
        matches = c.data["matches"]
        if not matches:
            ev.append({"source": "search_catalog", "finding": "No catalog product with the same name, vendor or category.", "reference": None})
        for m in matches:
            ev.append({
                "source": "search_catalog",
                "finding": f"{m['product_name']} ({m['vendor_name']}, {m['category']}) is in the catalog: status '{m['status']}', {m['licensed_seats']} seats, scope {m['scope']}; notes: {m['notes']}. Match: {', '.join(x.replace('_', ' ') for x in m['match_reasons'])}.",
                "reference": m["software_id"],
            })
    else:
        ev.append({"source": "search_catalog", "finding": f"Catalog search failed: {c.error}", "reference": None})

    r = tools["registry"]
    if r.ok and r.data:
        d = r.data
        ev.append({
            "source": "lookup_vendor_registry",
            "finding": f"Registry: {d['vendor_name']} procurement '{d['procurement_status']}', security '{d['security_status']}' (review date {d['security_review_date'] or 'none'}), legal terms '{d['legal_terms_status']}'; notes: {d['notes']}.",
            "reference": d["vendor_id"],
        })
    elif r.ok:
        ev.append({"source": "lookup_vendor_registry", "finding": f"Vendor '{request.get('vendor_name')}' is not in the internal registry.", "reference": None})
    else:
        ev.append({"source": "lookup_vendor_registry", "finding": f"Registry lookup failed: {r.error}", "reference": None})

    v = tools["vendor_risk"]
    vd = v.data or {}
    if v.ok and vd.get("status") == "found":
        ev.append({
            "source": "get_vendor_risk",
            "finding": f"Vendor-risk API: risk '{vd.get('risk_level')}', security review '{vd.get('security_review_status')}' (last review {vd.get('last_review_date') or 'none'}), processes personal data: {vd.get('processes_personal_data')}, stores data outside region: {vd.get('stores_data_outside_region')}; notes: {vd.get('notes')}.",
            "reference": v.references[0],
        })
    elif v.ok and vd.get("status") == "not_found":
        ev.append({"source": "get_vendor_risk", "finding": "Vendor-risk API has no record for this vendor.", "reference": v.references[0]})
    else:
        ev.append({"source": "get_vendor_risk", "finding": f"Vendor-risk API unavailable ({v.error}); external risk status NOT verified.", "reference": v.references[0] if v.references else None})

    ph = tools["purchase_history"]
    if ph.ok:
        for p in ph.data["purchases"]:
            ev.append({
                "source": "lookup_purchase_history",
                "finding": f"{p['purchase_id']} on {p['purchase_date']}: {p['department']} bought {p['product_name']} for {_money(p['annual_amount_usd'])}/yr ({p['status']}; {p['notes']}).",
                "reference": p["purchase_id"],
            })
    return ev


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
def evaluate_rules(request: dict, tools: dict[str, ToolResult], injection_hits: list[dict] | None = None) -> RulesOutcome:
    out = RulesOutcome()
    out.evidence.extend(_tool_evidence(request, tools))

    cost = request.get("annual_cost_usd")
    data_level = _norm(request.get("data_access_level"))
    data_unknown = data_level in UNKNOWN_VALUES
    integrations = [_norm(i) for i in (request.get("requested_integrations") or [])]
    is_pii = any(k in data_level for k in PII_KEYWORDS)
    data_sensitive = any(k in data_level for k in SECURITY_DATA_KEYWORDS)
    sensitive_integrations = [i for i in integrations if any(k in i for k in SECURITY_INTEGRATION_KEYWORDS)]

    # §1 Required information ------------------------------------------------
    emp = tools["employee"]
    if not (emp.ok and emp.data):
        out.missing_information.append("requester and department (requester not found)")
    if not request.get("product_name") or not request.get("vendor_name"):
        out.missing_information.append("product/vendor")
    if cost is None:
        out.missing_information.append("annual cost or reasonable annual estimate (needed for budget check and approval tier)")
    if request.get("user_count") is None:
        out.missing_information.append("number of users/licenses")
    if not _norm(request.get("business_justification")):
        out.missing_information.append("business purpose")
    if data_unknown:
        out.missing_information.append("intended data-access level")
    if request.get("requested_integrations") is None:
        out.missing_information.append("required integrations")
    if out.missing_information:
        out.add_flag("missing_information")
        out.add_reason("missing_information", "Request is not ready for approval; missing: " + "; ".join(out.missing_information) + ".", "Policy §1")

    # §4 Financial thresholds -----------------------------------------------
    for a in tools["thresholds"].data["minimum_approvals"]:
        out.add_approval(a)

    # §2 Budget ---------------------------------------------------------------
    b = tools["budget"].data or {}
    if b.get("status") == "insufficient":
        out.add_flag("budget_insufficient")
        out.add_approval("Finance")
        out.add_reason("budget", f"Cost exceeds {b['department']} available software budget by {_money(b['shortfall_usd'])}; Finance budget-exception review required.", "Policy §2")

    # §3 Overlap --------------------------------------------------------------
    catalog_matches = (tools["catalog"].data or {}).get("matches", []) if tools["catalog"].ok else []
    product = _norm(request.get("product_name"))
    overlaps = [m for m in catalog_matches if "same_category" in m["match_reasons"] or _norm(m["product_name"]) == product]
    if overlaps:
        out.add_flag("existing_tool_overlap")
        names = ", ".join(f"{m['product_name']} ({m['software_id']})" for m in overlaps)
        out.add_reason("overlap", f"Existing catalog product(s) in the same category/product: {names}. Confirm a credible gap before buying.", "Policy §3")

    # Vendor assessment state (registry vs API) --------------------------------
    reg = tools["registry"].data if tools["registry"].ok else None
    risk_tool = tools["vendor_risk"]
    risk = risk_tool.data or {}
    api_status = risk.get("status")
    api_available = risk_tool.ok and api_status in ("found", "not_found")

    reg_approved = reg is not None and _norm(reg.get("security_status")) == "approved"
    reg_date = (reg or {}).get("security_review_date")
    api_approved = api_status == "found" and _norm(risk.get("security_review_status")) == "approved"
    api_date = risk.get("last_review_date") if api_status == "found" else None

    assessment_problems: list[str] = []
    expired = False
    if reg is None:
        assessment_problems.append("vendor not in internal registry")
    elif not reg_approved:
        assessment_problems.append(f"registry security status is '{reg.get('security_status')}'")
    elif not reg_date:
        assessment_problems.append("registry has no security review date")
    elif not is_review_current(reg_date):
        expired = True
        assessment_problems.append(f"registry review date {reg_date} is {review_age_days(reg_date)} days old (> {REVIEW_VALID_DAYS})")

    if api_status == "found":
        api_review = _norm(risk.get("security_review_status"))
        if api_review == "expired":
            expired = True
            assessment_problems.append("vendor-risk API reports the security review as expired")
        elif api_review != "approved":
            assessment_problems.append(f"vendor-risk API security review status is '{risk.get('security_review_status')}'")
        elif api_date and not is_review_current(api_date):
            expired = True
            assessment_problems.append(f"vendor-risk API review date {api_date} is older than {REVIEW_VALID_DAYS} days")
    elif api_status == "not_found":
        assessment_problems.append("vendor-risk API has no assessment for this vendor")

    if not api_available:
        out.add_flag("vendor_risk_unavailable")
        out.add_reason("vendor_risk_unavailable", "Vendor-risk service unavailable; external security status could not be verified and must not be assumed favourable. Manual Security review required.", "Policy §10")

    conflict = False
    if reg is not None and api_status == "found":
        if reg_approved != api_approved or (reg_date and api_date and str(reg_date) != str(api_date)):
            conflict = True
            out.add_flag("conflicting_vendor_evidence")
            out.add_reason("conflict", f"Registry says security '{reg.get('security_status')}' ({reg_date or 'no date'}) but vendor-risk API says '{risk.get('security_review_status')}' ({api_date or 'no date'}). Conflict surfaced, not resolved; route to Security.", "Policy §5")

    if expired:
        out.add_flag("vendor_review_expired")

    # §5 Security -------------------------------------------------------------
    security_reasons = []
    if data_sensitive:
        security_reasons.append(f"data access level '{request.get('data_access_level')}' is sensitive")
    if sensitive_integrations:
        security_reasons.append(f"integration(s) {', '.join(request.get('requested_integrations'))} touch production/code/credentials")
    if data_unknown:
        security_reasons.append("data access level is unknown, so sensitive access cannot be ruled out")
    security_reasons.extend(assessment_problems)
    if not api_available:
        security_reasons.append("vendor security status could not be verified (API unavailable)")
    if conflict:
        security_reasons.append("registry and vendor-risk API disagree")
    if security_reasons:
        out.add_flag("security_review_required")
        out.add_approval("Security")
        out.add_reason("security", "Security review required: " + "; ".join(security_reasons) + ".", "Policy §5")

    # §6 Privacy --------------------------------------------------------------
    outside_region = api_status == "found" and bool(risk.get("stores_data_outside_region"))
    privacy_reasons = []
    if is_pii:
        privacy_reasons.append(f"tool will process {request.get('data_access_level')}")
    if outside_region and (data_sensitive or data_unknown):
        privacy_reasons.append("vendor stores data outside the operating region and the data may be sensitive")
    if privacy_reasons:
        out.add_flag("privacy_review_required")
        out.add_approval("Privacy")
        out.add_reason("privacy", "Privacy review required: " + "; ".join(privacy_reasons) + ".", "Policy §6")

    # §7 Legal ----------------------------------------------------------------
    legal_reasons = []
    vendor_is_new = reg is None or _norm(reg.get("procurement_status")) != "approved"
    if vendor_is_new and cost is not None and float(cost) >= LEGAL_NEW_VENDOR_THRESHOLD:
        legal_reasons.append(f"new / not-onboarded vendor with annual spend {_money(float(cost))} (>= {_money(LEGAL_NEW_VENDOR_THRESHOLD)})")
    if reg is None or _norm(reg.get("legal_terms_status")) not in ("approved", "standard"):
        legal_reasons.append(f"legal terms status is '{(reg or {}).get('legal_terms_status', 'unknown')}'")
    if is_pii and outside_region:
        legal_reasons.append("personal data would be stored outside the operating region (cross-region data issue)")
    if legal_reasons:
        out.add_flag("legal_review_required")
        out.add_approval("Legal")
        out.add_reason("legal", "Legal review required: " + "; ".join(legal_reasons) + ".", "Policy §7")
    elif vendor_is_new and cost is None:
        out.evidence.append({"source": "policy_rules", "finding": "New-vendor legal threshold cannot be evaluated until annual cost is provided.", "reference": "Policy §7"})

    # §9 Prompt injection ------------------------------------------------------
    if injection_hits:
        out.add_flag("prompt_injection_detected")
        where = "; ".join(f"{h['location']}: \"{h['match']}\"" for h in injection_hits)
        out.add_reason("prompt_injection", f"Instruction-like text in untrusted business data was ignored ({where}). Policy and evidence still applied in full.", "Policy §9")

    out.approvals = sort_approvals(out.approvals)

    # Deterministic default next action (used by rules-only mode and LLM fallback)
    if out.missing_information:
        out.default_action = "request_clarification"
    elif any(f in out.flags for f in ("budget_insufficient", "security_review_required", "privacy_review_required",
                                       "legal_review_required", "vendor_risk_unavailable", "conflicting_vendor_evidence",
                                       "vendor_review_expired", "existing_tool_overlap", "prompt_injection_detected")):
        out.default_action = "route_for_review"
    else:
        out.default_action = "proceed_to_approval"

    out.facts = {
        "data_access_level": request.get("data_access_level"),
        "data_is_sensitive": data_sensitive,
        "data_is_pii": is_pii,
        "vendor_is_new": vendor_is_new,
        "vendor_stores_data_outside_region": outside_region if api_available else "unverified",
        "vendor_assessment_problems": assessment_problems,
        "overlapping_catalog_products": [m["software_id"] for m in overlaps],
    }
    return out
