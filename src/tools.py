"""Evidence-gathering tools.

Every tool is plain Python, is called by the orchestrator (never by the model),
and returns a ToolResult. A tool never raises: failures come back as
ok=False with an error message so that downstream rules can treat them as
"could not verify" instead of crashing or inferring a favourable status.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd
import requests

from src.data_access import (
    load_budgets,
    load_employees,
    load_purchase_history,
    load_software_catalog,
    load_vendors,
)
from src.vendor_client import get_vendor_risk


@dataclass
class ToolResult:
    tool: str
    ok: bool
    data: Any = None
    error: str | None = None
    references: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "references": self.references,
        }


def _safe(tool_name: str, fn: Callable[[], ToolResult]) -> ToolResult:
    try:
        return fn()
    except Exception as exc:  # data file missing, malformed CSV, etc.
        return ToolResult(tool=tool_name, ok=False, error=f"{type(exc).__name__}: {exc}")


def _clean(value: Any) -> Any:
    """Convert pandas NaN to None so tool output is JSON-safe."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value.item() if hasattr(value, "item") else value


def _norm(text: Any) -> str:
    return str(text or "").strip().lower()


# ---------------------------------------------------------------------------
# 1. Employee lookup
# ---------------------------------------------------------------------------
def lookup_employee(requester_id: str | None) -> ToolResult:
    name = "lookup_employee"

    def run() -> ToolResult:
        df = load_employees()
        row = df[df["employee_id"] == requester_id]
        if row.empty:
            return ToolResult(name, ok=True, data=None, references=[])
        r = row.iloc[0]
        data = {k: _clean(r[k]) for k in ["employee_id", "name", "department", "manager_id", "level", "country"]}
        return ToolResult(name, ok=True, data=data, references=[str(r["employee_id"])])

    return _safe(name, run)


# ---------------------------------------------------------------------------
# 2. Budget check (deterministic)
# ---------------------------------------------------------------------------
def check_budget(department: str | None, annual_cost: float | None) -> ToolResult:
    name = "check_budget"

    def run() -> ToolResult:
        if not department:
            return ToolResult(name, ok=True, data={"status": "cannot_check", "reason": "requester department unknown"})
        if annual_cost is None:
            return ToolResult(name, ok=True, data={"status": "cannot_check", "reason": "annual cost missing", "department": department})
        df = load_budgets()
        row = df[df["department"].str.lower() == department.lower()]
        if row.empty:
            return ToolResult(name, ok=True, data={"status": "department_not_found", "department": department})
        available = float(row.iloc[0]["available_usd"])
        sufficient = float(annual_cost) <= available
        return ToolResult(
            name,
            ok=True,
            data={
                "status": "sufficient" if sufficient else "insufficient",
                "department": department,
                "requested_usd": float(annual_cost),
                "available_usd": available,
                "shortfall_usd": 0.0 if sufficient else round(float(annual_cost) - available, 2),
            },
            references=[f"department_budgets:{department}"],
        )

    return _safe(name, run)


# ---------------------------------------------------------------------------
# 3. Approval thresholds (deterministic, policy §4)
# ---------------------------------------------------------------------------
def approval_thresholds(annual_cost: float | None) -> ToolResult:
    name = "approval_thresholds"
    if annual_cost is None:
        return ToolResult(
            name,
            ok=True,
            data={"tier": "undetermined", "minimum_approvals": [], "reason": "annual cost missing"},
            references=["Policy §4"],
        )
    cost = float(annual_cost)
    if cost <= 1000:
        tier, approvals = "<= $1,000", ["Manager"]
    elif cost <= 10000:
        tier, approvals = "$1,000.01 - $10,000", ["Department Head", "Procurement"]
    elif cost <= 25000:
        tier, approvals = "$10,000.01 - $25,000", ["Department Head", "Finance", "Procurement"]
    else:
        tier, approvals = "> $25,000", ["Department Head", "Finance", "CFO", "Procurement"]
    return ToolResult(name, ok=True, data={"tier": tier, "minimum_approvals": approvals}, references=["Policy §4"])


# ---------------------------------------------------------------------------
# 4. Software catalog search
# ---------------------------------------------------------------------------
def search_catalog(product_name: str | None, vendor_name: str | None, category: str | None) -> ToolResult:
    """Find catalog entries related to the request.

    match_type:
      - "same_category": functional overlap (drives the existing_tool_overlap flag)
      - "same_product":  requested product name contains / is contained in a catalog product
      - "same_vendor":   existing vendor relationship only
    """
    name = "search_catalog"

    def run() -> ToolResult:
        df = load_software_catalog()
        p, v, c = _norm(product_name), _norm(vendor_name), _norm(category)
        matches = []
        for _, r in df.iterrows():
            rp, rv, rc = _norm(r["product_name"]), _norm(r["vendor_name"]), _norm(r["category"])
            reasons = []
            if c and c == rc:
                reasons.append("same_category")
            if p and rp and (rp in p or p in rp):
                reasons.append("same_product")
            if v and v == rv:
                reasons.append("same_vendor")
            if reasons:
                matches.append({
                    "software_id": str(r["software_id"]),
                    "product_name": str(r["product_name"]),
                    "vendor_name": str(r["vendor_name"]),
                    "category": str(r["category"]),
                    "status": str(r["status"]),
                    "licensed_seats": _clean(r["licensed_seats"]),
                    "scope": str(r["scope"]),
                    "notes": str(_clean(r["notes"]) or ""),
                    "match_reasons": reasons,
                })
        return ToolResult(name, ok=True, data={"matches": matches}, references=[m["software_id"] for m in matches])

    return _safe(name, run)


# ---------------------------------------------------------------------------
# 5. Internal vendor registry
# ---------------------------------------------------------------------------
def lookup_vendor_registry(vendor_name: str | None) -> ToolResult:
    name = "lookup_vendor_registry"

    def run() -> ToolResult:
        df = load_vendors()
        row = df[df["vendor_name"].str.lower() == _norm(vendor_name)]
        if row.empty:
            return ToolResult(name, ok=True, data=None)
        r = row.iloc[0]
        data = {k: _clean(r[k]) for k in [
            "vendor_id", "vendor_name", "procurement_status", "security_status",
            "security_review_date", "legal_terms_status", "notes",
        ]}
        return ToolResult(name, ok=True, data=data, references=[str(r["vendor_id"])])

    return _safe(name, run)


# ---------------------------------------------------------------------------
# 6. External vendor-risk API
# ---------------------------------------------------------------------------
def get_vendor_risk_status(vendor_name: str | None) -> ToolResult:
    """Call the vendor-risk service. Outages and unknown vendors are explicit statuses, never exceptions."""
    name = "get_vendor_risk"
    if not vendor_name:
        return ToolResult(name, ok=False, data={"status": "not_queried"}, error="vendor name missing")
    ref = f"vendor-risk-api:/vendor-risk/{vendor_name}"
    try:
        data = get_vendor_risk(vendor_name)
        return ToolResult(name, ok=True, data={"status": "found", **data}, references=[ref])
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else None
        if code == 404:
            return ToolResult(name, ok=True, data={"status": "not_found"}, references=[ref])
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            pass
        return ToolResult(name, ok=False, data={"status": "unavailable", "http_status": code},
                          error=f"HTTP {code}: {detail}".strip(), references=[ref])
    except requests.RequestException as exc:
        return ToolResult(name, ok=False, data={"status": "unavailable"},
                          error=f"{type(exc).__name__}: service unreachable or timed out", references=[ref])
    except Exception as exc:
        return ToolResult(name, ok=False, data={"status": "unavailable"}, error=f"{type(exc).__name__}: {exc}", references=[ref])


# ---------------------------------------------------------------------------
# 7. Purchase history
# ---------------------------------------------------------------------------
def lookup_purchase_history(vendor_name: str | None) -> ToolResult:
    name = "lookup_purchase_history"

    def run() -> ToolResult:
        df = load_purchase_history()
        rows = df[df["vendor_name"].str.lower() == _norm(vendor_name)]
        purchases = [{k: _clean(r[k]) for k in df.columns} for _, r in rows.iterrows()]
        return ToolResult(name, ok=True, data={"purchases": purchases}, references=[p["purchase_id"] for p in purchases])

    return _safe(name, run)


# ---------------------------------------------------------------------------
# Orchestration: run every tool for a request
# ---------------------------------------------------------------------------
def gather_evidence(request: dict) -> dict[str, ToolResult]:
    employee = lookup_employee(request.get("requester_id"))
    department = (employee.data or {}).get("department") if employee.ok else None
    cost = request.get("annual_cost_usd")
    vendor = request.get("vendor_name")
    return {
        "employee": employee,
        "budget": check_budget(department, cost),
        "thresholds": approval_thresholds(cost),
        "catalog": search_catalog(request.get("product_name"), vendor, request.get("category")),
        "registry": lookup_vendor_registry(vendor),
        "vendor_risk": get_vendor_risk_status(vendor),
        "purchase_history": lookup_purchase_history(vendor),
    }
