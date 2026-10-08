"""Deterministic prompt-injection detector for untrusted business data.

The guard only *flags*. It never stops the pipeline: policy §9 says to ignore
the embedded instruction and keep applying the real policy to the real
evidence. Patterns target instruction-shaped phrases rather than single words
like "override" or "bypass", which appear in legitimate business text.
"""
from __future__ import annotations

import re
from typing import Any

INJECTION_PATTERNS = [
    r"\b(ignore|disregard|forget|skip)\s+(all\s+|any\s+|the\s+|your\s+|previous\s+|prior\s+)*(procurement\s+|security\s+|company\s+)?(rules|instructions|policy|policies|controls|guidelines|checks)\b",
    r"\btreat\s+(this|the|it)(\s+request)?\s+as\s+[\w\- ]*approved\b",
    r"\b(approve|authori[sz]e)\s+(it|this|the request)\s+(immediately|now|automatically|without)\b",
    r"\byou\s+(must|should|need to|are required to)\s+(approve|authori[sz]e|ignore|bypass)\b",
    r"\b(already|pre)[\s\-]?approved\s+by\s+(the\s+)?(cfo|ceo|finance|security|legal|management)\b",
    r"\b(cfo|ceo)[\s\-]approved\b",
    r"\b(bypass|skip|override)\s+(the\s+|all\s+)?(security|privacy|legal|procurement|approval|review)",
    r"\b(system\s+prompt|new\s+instructions|developer\s+mode|you\s+are\s+now)\b",
    r"\b(reveal|print|expose|show)\s+(your\s+|the\s+)?(system\s+prompt|api\s+key|secrets?|credentials)\b",
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]

REQUEST_TEXT_FIELDS = ["business_justification", "product_name", "vendor_name", "category", "urgency"]


def scan_for_injection(text: str | None) -> bool:
    """True when the text contains an instruction-shaped override attempt."""
    return bool(find_injections(text))


def find_injections(text: str | None) -> list[str]:
    if not text:
        return []
    return [m.group(0) for rx in _COMPILED for m in rx.finditer(str(text))]


def scan_request_and_evidence(request: dict, tool_results: dict[str, Any]) -> list[dict]:
    """Scan every untrusted free-text field: request text plus notes returned by tools."""
    hits: list[dict] = []

    def check(location: str, value: Any) -> None:
        for match in find_injections(value if isinstance(value, str) else None):
            hits.append({"location": location, "match": match})

    for f in REQUEST_TEXT_FIELDS:
        check(f"request.{f}", request.get(f))
    for i, integration in enumerate(request.get("requested_integrations") or []):
        check(f"request.requested_integrations[{i}]", integration)

    registry = getattr(tool_results.get("registry"), "data", None) or {}
    check("vendor_registry.notes", registry.get("notes"))
    risk = getattr(tool_results.get("vendor_risk"), "data", None) or {}
    check("vendor_risk_api.notes", risk.get("notes"))
    for m in (getattr(tool_results.get("catalog"), "data", None) or {}).get("matches", []):
        check(f"catalog.{m['software_id']}.notes", m.get("notes"))
    for p in (getattr(tool_results.get("purchase_history"), "data", None) or {}).get("purchases", []):
        check(f"purchase_history.{p.get('purchase_id')}.notes", p.get("notes"))
    return hits
