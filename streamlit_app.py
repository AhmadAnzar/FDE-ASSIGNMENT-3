"""Procurement Request Copilot - analyst workspace.

Layout: request header -> [decision | policy checks]. Details are one click away,
so the page stays readable at a glance. Run with: python run_local.py
"""
from __future__ import annotations

import html
import json
import os
from datetime import datetime
from pathlib import Path

import streamlit as st

from src.data_access import load_requests
from src.guard import find_injections
from src.solution import process_request_with_trace

ROOT = Path(__file__).resolve().parent
# Interactive use: never keep the analyst waiting on provider throttling; fall back to rules-only instead.
os.environ.setdefault("LLM_MAX_WAIT_S", "20")
DECISIONS_LOG = ROOT / "decisions_log.jsonl"

ACTIONS = {  # label, colour
    "proceed_to_approval": ("Proceed to business approval", "#34D399"),
    "reuse_existing_tool": ("Reuse an existing tool", "#38BDF8"),
    "route_for_review": ("Route for specialist review", "#FBBF24"),
    "request_clarification": ("Ask the requester for details", "#C4B5FD"),
}
ARCH_LABELS = {"single": "A · Single agent", "staged": "B · Analyst → Reviewer", "rules_only": "Rules only (no AI)"}
STATUS = {"ok": ("green", "✓"), "warn": ("orange", "▲"), "fail": ("red", "✕"), "info": ("blue", "•")}
SPECIALIST = {"Security", "Privacy", "Legal"}
NOT_REQUIRED = {
    "security": "No sensitive data or integrations, and the vendor security assessment is current in both the registry and the risk service.",
    "privacy": "No employee or customer PII, and no sensitive data stored outside the operating region.",
    "legal": "Vendor already onboarded (or spend below $10,000), legal terms approved, no cross-region personal data.",
}
FINANCIAL = {"Finance", "CFO"}

st.set_page_config(page_title="Procurement Copilot", page_icon=":clipboard:", layout="wide")
st.markdown("""
<style>
.block-container {padding-top: 2.6rem; padding-bottom: 2rem; max-width: 1280px;}
.pc-eyebrow {color: #7C879B; font-size: 0.8rem; margin-bottom: 0.1rem;}
.pc-h1 {font-size: 1.7rem; font-weight: 700; margin: 0 0 0.25rem; color: #F1F4F9;}
.pc-meta {color: #A3ADBF; font-size: 0.9rem;}
.pc-meta span + span::before {content: "·"; margin: 0 0.45rem; color: #4B5468;}
.pc-quote {border-left: 3px solid #3A4357; padding: 0.15rem 0 0.15rem 0.8rem; margin: 0.7rem 0 0.2rem;
           color: #C9D1DE; font-style: italic; font-size: 0.92rem;}
.pc-quote small {display: block; font-style: normal; color: #6F7A8E; font-size: 0.72rem; margin-top: 0.15rem;}
.pc-warn {color: #FCA5A5; font-size: 0.85rem; margin-top: 0.35rem;}
.pc-pill {display: inline-block; font-size: 0.75rem; font-weight: 700; letter-spacing: 0.04em;
          border-radius: 999px; padding: 0.2rem 0.65rem; margin-bottom: 0.5rem;}
.pc-headline {font-size: 1.35rem; font-weight: 700; color: #F1F4F9; margin: 0 0 0.4rem; line-height: 1.3;}
.pc-body {color: #C9D1DE; font-size: 0.95rem; line-height: 1.55; margin-bottom: 0.8rem;}
.pc-section {color: #7C879B; font-size: 0.8rem; font-weight: 600; margin: 1rem 0 0.4rem;}
.pc-next {color: #E6E9EF; font-size: 0.92rem; line-height: 1.5;}
.pc-chip {display: inline-block; border-radius: 8px; padding: 0.22rem 0.6rem; margin: 0 0.35rem 0.35rem 0;
          font-size: 0.82rem; font-weight: 600;}
.pc-why {margin: 0; padding-left: 1.1rem; color: #C9D1DE; font-size: 0.9rem; line-height: 1.55;}
.pc-why li {margin-bottom: 0.25rem;}
.pc-ref {font-family: ui-monospace, Consolas, monospace; font-size: 0.72rem; color: #8FA3FF; margin-left: 0.3rem;}
.pc-tel {color: #6F7A8E; font-size: 0.75rem; margin-top: 0.9rem;}
.pc-detail {color: #C9D1DE; font-size: 0.86rem; line-height: 1.5; margin-bottom: 0.45rem;}
.pc-summary {color: #A3ADBF; font-size: 0.85rem; margin: -0.2rem 0 0.6rem;}
.pc-empty {color: #7C879B; font-size: 0.95rem; padding: 2.5rem 0; text-align: center;}
div[data-testid="stExpander"] details {border-color: #1F2635;}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Helpers - every business string is HTML-escaped (it is untrusted data)
# ---------------------------------------------------------------------------
def esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def money(v) -> str:
    return f"${v:,.0f}" if isinstance(v, (int, float)) else "cost not provided"


def chip(text: str, color: str) -> str:
    return f'<span class="pc-chip" style="color:{color};background:{color}1F">{esc(text)}</span>'


def md_safe(text: str) -> str:
    """Neutralise Markdown/colour syntax before putting text into a widget label."""
    return "".join("\\" + c if c in "\\`*_[]{}()#+-.!:|<>~$" else c for c in str(text))


def reasons_from(finding: str | None) -> list[str]:
    if not finding:
        return []
    body = finding.split(":", 1)[1] if ":" in finding else finding
    return [r.strip().rstrip(".") for r in body.split(";") if r.strip()]


def short(text: str, n: int = 70) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def load_log() -> list[dict]:
    if not DECISIONS_LOG.exists():
        return []
    rows = []
    for line in DECISIONS_LOG.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def append_log(entry: dict) -> None:
    with DECISIONS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Turn tool + rule output into a short checklist
# ---------------------------------------------------------------------------
def build_checks(req: dict, decision, trace: dict) -> list[dict]:
    tools, rules = trace["tools"], trace["rules"]
    flags = set(decision.risk_flags)
    rule = {f["rule"]: f["finding"] for f in rules["rule_findings"]}
    ev = lambda *src: [(e.finding, e.reference) for e in decision.evidence if e.source in src]  # noqa: E731
    checks = []

    # Completeness
    if decision.missing_information:
        names = []
        for m in decision.missing_information:
            low = m.lower()
            names.append("annual cost" if "cost" in low else "users" if "user" in low else "data access" if "data" in low
                         else "requester" if "requester" in low else short(m, 25))
        checks.append(dict(status="warn", title="Request completeness", summary="Missing " + ", ".join(names),
                           details=[(m, "Policy §1") for m in decision.missing_information]))
    else:
        checks.append(dict(status="ok", title="Request completeness", summary="All required fields provided",
                           details=[("Checked: requester and department, product/vendor, annual cost, users, business purpose, data-access level, integrations.", "Policy §1")]))

    # Budget
    b = tools["budget"]["data"] or {}
    if b.get("status") == "sufficient":
        checks.append(dict(status="ok", title="Budget", summary=f"{money(b['requested_usd'])} of {money(b['available_usd'])} available · {b['department']}", details=ev("check_budget")))
    elif b.get("status") == "insufficient":
        checks.append(dict(status="fail", title="Budget", summary=f"Over by {money(b['shortfall_usd'])} · only {money(b['available_usd'])} available", details=ev("check_budget") + [(rule.get("budget", ""), "Policy §2")]))
    else:
        checks.append(dict(status="warn", title="Budget", summary="Not checked · " + str(b.get("reason") or b.get("status")), details=ev("check_budget")))

    # Approval tier
    t = tools["thresholds"]["data"]
    if t["tier"] == "undetermined":
        checks.append(dict(status="warn", title="Approval tier", summary="Undetermined until cost is provided", details=ev("approval_thresholds")))
    else:
        checks.append(dict(status="info", title="Approval tier", summary=f"{t['tier']} → {', '.join(t['minimum_approvals'])}", details=ev("approval_thresholds")))

    # Existing tools
    matches = (tools["catalog"]["data"] or {}).get("matches", [])
    overlap_ids = set(rules["facts"].get("overlapping_catalog_products", []))
    overlap_names = [m["product_name"] for m in matches if m["software_id"] in overlap_ids]
    ai_overlap = [(f, r) for f, r in ev("ai_assessment") if f.startswith("Overlap assessment")]
    if overlap_names:
        checks.append(dict(status="warn", title="Existing tools", summary="Overlaps " + ", ".join(overlap_names),
                           details=ev("search_catalog", "lookup_purchase_history") + ai_overlap))
    elif matches:
        checks.append(dict(status="ok", title="Existing tools", summary="Same vendor only, different product", details=ev("search_catalog", "lookup_purchase_history")))
    else:
        checks.append(dict(status="ok", title="Existing tools", summary="No similar tool in the catalog", details=ev("search_catalog")))

    # Vendor status
    reg = tools["registry"]["data"] or {}
    risk = tools["vendor_risk"]["data"] or {}
    problems = rules["facts"].get("vendor_assessment_problems", [])
    vendor_details = ev("lookup_vendor_registry", "get_vendor_risk") + [(rule[k], None) for k in ("conflict", "vendor_risk_unavailable") if k in rule]
    if "conflicting_vendor_evidence" in flags:
        status, summary = "fail", "Registry and risk service disagree"
    elif "vendor_risk_unavailable" in flags:
        status, summary = "fail", "Risk service unavailable · not verified"
    elif "vendor_review_expired" in flags:
        status, summary = "fail", "Security review expired"
    elif problems:
        status, summary = "warn", short(problems[0][0].upper() + problems[0][1:], 60)
    else:
        status = "ok"
        summary = f"Approved · reviewed {reg.get('security_review_date') or '-'} · {risk.get('risk_level', '-')} risk"
    checks.append(dict(status=status, title="Vendor status", summary=summary, details=vendor_details))

    # Specialist reviews
    for key, title, flag in (("security", "Security review", "security_review_required"),
                             ("privacy", "Privacy review", "privacy_review_required"),
                             ("legal", "Legal review", "legal_review_required")):
        reasons = reasons_from(rule.get(key))
        if flag in flags:
            extra = f" (+{len(reasons) - 1} more)" if len(reasons) > 1 else ""
            summary = (short(reasons[0][0].upper() + reasons[0][1:], 55) + extra) if reasons else "Required"
            checks.append(dict(status="warn", title=title, summary=summary, details=[(r, None) for r in reasons]))
        else:
            checks.append(dict(status="ok", title=title, summary="Not required", details=[(NOT_REQUIRED[key], None)]))

    # Untrusted text
    if "prompt_injection_detected" in flags:
        checks.append(dict(status="fail", title="Untrusted text", summary="Instruction-like text found and ignored",
                           details=[(rule.get("prompt_injection", "Flagged by the AI reviewer."), "Policy §9")]))
    else:
        checks.append(dict(status="ok", title="Untrusted text", summary="No instruction-like text",
                           details=[("Scanned the request text and the notes returned by the registry, vendor-risk API, catalog and purchase history.", "Policy §9")]))
    return checks


# ---------------------------------------------------------------------------
# Sidebar: queue + new request
# ---------------------------------------------------------------------------
requests_by_id = {r["request_id"]: r for r in load_requests()}
st.session_state.setdefault("custom_requests", {})
st.session_state.setdefault("results", {})
all_requests = {**requests_by_id, **st.session_state.custom_requests}

with st.sidebar:
    st.markdown("### Request queue")
    request_id = st.selectbox("Request", list(all_requests.keys()),
                              format_func=lambda rid: f"{rid} · {all_requests[rid]['product_name']}")
    architecture = st.radio("Architecture", list(ARCH_LABELS), format_func=ARCH_LABELS.get)
    run = st.button("Analyze request", type="primary", use_container_width=True)
    st.caption("Single agent is the shipped design. Rules only = no AI.")
    st.divider()
    with st.expander("Add a request"):
        with st.form("new_request"):
            c_requester = st.text_input("Requester ID", "E001")
            c_product = st.text_input("Product")
            c_vendor = st.text_input("Vendor")
            c_category = st.text_input("Category")
            c_cost = st.number_input("Annual cost (USD, 0 = unknown)", min_value=0.0, step=100.0)
            c_users = st.number_input("Users (0 = unknown)", min_value=0, step=1)
            c_data = st.selectbox("Data access level", ["unknown", "none", "internal_documents", "internal_marketing",
                                                        "confidential_documents", "source_code", "production_telemetry",
                                                        "employee_pii", "customer_pii", "credentials"])
            c_integrations = st.text_input("Integrations (comma separated)")
            c_justification = st.text_area("Business justification")
            if st.form_submit_button("Add to queue", use_container_width=True):
                rid = f"NEW-{len(st.session_state.custom_requests) + 1:03d}"
                st.session_state.custom_requests[rid] = {
                    "request_id": rid, "requester_id": c_requester.strip(), "product_name": c_product.strip() or "Untitled",
                    "vendor_name": c_vendor.strip(), "category": c_category.strip(),
                    "annual_cost_usd": c_cost or None, "user_count": int(c_users) or None,
                    "business_justification": c_justification.strip(), "data_access_level": c_data,
                    "requested_integrations": [i.strip() for i in c_integrations.split(",") if i.strip()],
                    "urgency": "normal",
                }
                st.rerun()

req = all_requests[request_id]

# ---------------------------------------------------------------------------
# Header: request + controls
# ---------------------------------------------------------------------------
with st.container():
    meta = [req.get("vendor_name"), req.get("category"),
            f"{money(req.get('annual_cost_usd'))} / yr" if req.get("annual_cost_usd") is not None else "cost not provided",
            f"{req['user_count']} users" if req.get("user_count") else "users not provided",
            f"data: {req.get('data_access_level')}",
            "integrations: " + (", ".join(req.get("requested_integrations") or []) or "none")]
    st.markdown(
        f'<div class="pc-eyebrow">{esc(req["request_id"])} · requested by {esc(req.get("requester_id"))} · urgency {esc(req.get("urgency", "-"))}</div>'
        f'<div class="pc-h1">{esc(req.get("product_name"))}</div>'
        '<div class="pc-meta">' + "".join(f"<span>{esc(m)}</span>" for m in meta) + "</div>"
        f'<div class="pc-quote">“{esc(req.get("business_justification") or "No justification given")}”'
        "<small>Requester's words · treated as data, never as instructions</small></div>",
        unsafe_allow_html=True)
    hits = find_injections(req.get("business_justification"))
    if hits:
        st.markdown('<div class="pc-warn">✕ Instruction-like text detected and ignored: '
                    + "; ".join(f"“{esc(h)}”" for h in hits) + "</div>", unsafe_allow_html=True)

result_key = f"{request_id}|{architecture}"
if run:
    with st.spinner("Gathering evidence, applying policy, asking the AI reviewer…"):
        try:
            st.session_state.results[result_key] = process_request_with_trace(request_id, req, architecture)
        except Exception as exc:
            st.error(f"Analysis failed: {type(exc).__name__}: {exc}")
result = st.session_state.results.get(result_key)

st.write("")
left, right = st.columns([1.15, 1], gap="large")

if not result:
    with left:
        st.markdown('<div class="pc-empty">Click <b>Analyze request</b> to gather evidence and get a recommendation.</div>',
                    unsafe_allow_html=True)
    st.stop()

decision, trace = result

# ---------------------------------------------------------------------------
# Left: decision
# ---------------------------------------------------------------------------
with left:
    label, color = ACTIONS.get(decision.recommended_action, ("Recommendation", "#94A3B8"))
    rec_text = decision.recommendation.split(": ", 1)[-1]
    st.markdown(
        f'<span class="pc-pill" style="color:{color};background:{color}22">● RECOMMENDATION</span>'
        f'<div class="pc-headline">{esc(label)}</div>'
        f'<div class="pc-body">{esc(rec_text)}</div>'
        f'<div class="pc-section">Next step</div><div class="pc-next">→ {esc(decision.next_step)}</div>',
        unsafe_allow_html=True)
    if "llm_unavailable" in decision.risk_flags:
        st.markdown('<div class="pc-warn">AI reviewer unavailable (' + esc(decision.telemetry.llm_error) + ') · this recommendation comes from the deterministic rules only.</div>',
                    unsafe_allow_html=True)

    chips = "".join(chip(a, "#5EEAD4" if a in SPECIALIST else "#FCD34D" if a in FINANCIAL else "#A5B4FC")
                    for a in decision.required_approvals) or chip("Tier undetermined until cost is known", "#C4B5FD")
    st.markdown(f'<div class="pc-section">Approvals needed</div>{chips}', unsafe_allow_html=True)

    why = [(e.finding, e.reference) for e in decision.evidence
           if e.source == "ai_assessment" and not e.finding.startswith(("Overlap assessment", "Added "))]
    if why:
        st.markdown('<div class="pc-section">Why (AI reviewer)</div><ul class="pc-why">'
                    + "".join(f"<li>{esc(f)}{f'<span class=pc-ref>{esc(r)}</span>' if r else ''}</li>" for f, r in why[:4])
                    + "</ul>", unsafe_allow_html=True)

    t = decision.telemetry
    st.markdown(
        f'<div class="pc-tel">{esc(ARCH_LABELS.get(t.architecture, t.architecture))} · {t.latency_ms / 1000:.1f}s'
        + (f" ({t.retry_wait_ms / 1000:.1f}s waiting on rate limit)" if t.retry_wait_ms else "")
        + f" · {t.llm_calls} LLM call{'s' if t.llm_calls != 1 else ''} · {t.tool_calls} tool calls"
        + (f" · {(t.prompt_tokens or 0) + (t.completion_tokens or 0):,} tokens" if t.llm_calls else "") + "</div>",
        unsafe_allow_html=True)

    st.markdown('<div class="pc-section">Your decision</div>', unsafe_allow_html=True)
    with st.form(f"decide_{result_key}", border=False):
        choice = st.segmented_control("Decision", ["Approve my step", "Request info", "Escalate", "Reject"],
                                      label_visibility="collapsed")
        c1, c2 = st.columns([1, 2])
        reviewer = c1.text_input("Reviewer", placeholder="Your name", label_visibility="collapsed")
        comment = c2.text_input("Comment", placeholder="Comment or exception rationale (optional)", label_visibility="collapsed")
        if st.form_submit_button("Record decision", use_container_width=True):
            if not choice or not reviewer.strip():
                st.warning("Pick a decision and enter your name.")
            else:
                overrides = (choice == "Approve my step" and decision.recommended_action != "proceed_to_approval") or \
                            (choice == "Reject" and decision.recommended_action == "proceed_to_approval")
                append_log({"timestamp": datetime.now().isoformat(timespec="seconds"), "request_id": request_id,
                            "reviewer": reviewer.strip(), "decision": choice, "comment": comment.strip(),
                            "copilot_action": decision.recommended_action, "architecture": architecture,
                            "copilot_required_approvals": decision.required_approvals, "overrides_copilot": overrides})
                st.success("Recorded" + (" · marked as overriding the copilot" if overrides else ""))
    history = [e for e in load_log() if e["request_id"] == request_id]
    if history:
        last = history[-1]
        st.caption(f"Last decision: {last['decision']} by {last['reviewer']} at {last['timestamp']}"
                   + (f" · {len(history)} total" if len(history) > 1 else ""))

# ---------------------------------------------------------------------------
# Right: policy checks
# ---------------------------------------------------------------------------
with right:
    checks = build_checks(req, decision, trace)
    attention = sum(1 for c in checks if c["status"] in ("warn", "fail"))
    st.markdown("#### Policy checks")
    st.markdown(f'<div class="pc-summary">{attention} need attention · {len(checks) - attention} clear · click a check for its evidence</div>',
                unsafe_allow_html=True)
    for c in checks:
        col, sym = STATUS[c["status"]]
        label = f":{col}[{sym}]  **{c['title']}**  :gray[{md_safe(c['summary'])}]"
        with st.expander(label, expanded=False):
            if not c["details"]:
                st.markdown('<div class="pc-detail">Nothing to report.</div>', unsafe_allow_html=True)
            for finding, ref in c["details"]:
                if finding:
                    st.markdown(f'<div class="pc-detail">{esc(finding)}{f"<span class=pc-ref>{esc(ref)}</span>" if ref else ""}</div>',
                                unsafe_allow_html=True)

    with st.expander(":gray[Audit trail · raw tool outputs]"):
        for r in trace["tools"].values():
            st.markdown(f"**{r['tool']}** · {'ok' if r['ok'] else ':red[failed]'}")
            st.json(r, expanded=False)
        if trace.get("analyst_pack"):
            st.markdown("**Stage 1 · Analyst evidence pack**")
            st.json(trace["analyst_pack"], expanded=False)
        corrections = (trace.get("assessment") or {}).get("corrections_to_analyst")
        if corrections:
            st.markdown("**Stage 2 · Reviewer corrections**")
            for corr in corrections:
                st.markdown(f'<div class="pc-detail">{esc(corr)}</div>', unsafe_allow_html=True)
