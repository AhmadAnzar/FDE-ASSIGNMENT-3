# Workflow / Architecture Notes

## Workflow

```text
Purchase request  (untrusted text: justification, names, integrations)
      |
      v
[CODE] Gather evidence - 7 tools, always all of them, never raise
      |   lookup_employee · check_budget · approval_thresholds · search_catalog
      |   lookup_vendor_registry · get_vendor_risk (HTTP) · lookup_purchase_history
      v
[CODE] Injection guard - scans request text + every tool "notes" field
      |   flags prompt_injection_detected, never stops the pipeline (policy §9)
      v
[CODE] Rules engine - policy §1-§10 as plain if-statements
      |   -> required approvals, risk flags, missing information,
      |      evidence items with record IDs, default next action
      v
[LLM]  Judgment (one call in A; Analyst -> Reviewer in B)
      |   credible gap vs existing tools? AI data-class fit? next action + explanation
      v
[CODE] Merge + guardrails
      |   union with rules output (LLM can add, never remove) · action guardrails
      |   drop ungrounded claims · human_review_required = true
      |   LLM failure -> rules-only decision flagged llm_unavailable
      v
ProcurementDecision -> UI (decision | policy checks | audit trail)
      |
      v
Human decision: approve my step / request info / escalate / reject -> decisions_log.jsonl
```

Architectures A and B share every step except the LLM step, so the evaluation compares only the LLM setup.

## Tools

| Tool | Source | Deterministic? | On failure / no data |
|---|---|---|---|
| `lookup_employee` | employees.csv | lookup | unknown requester → missing information |
| `check_budget` | department_budgets.csv | **yes** | missing cost or department → `cannot_check` |
| `approval_thresholds` | policy §4 table | **yes** | missing cost → tier `undetermined` (never assumes the lowest tier) |
| `search_catalog` | software_catalog.csv | lookup | match types: same category / same product / same vendor |
| `lookup_vendor_registry` | vendors.csv | lookup | not in registry → treated as new and unassessed |
| `get_vendor_risk` | mock API `/vendor-risk/{name}` | external | 404 → `not_found`; 5xx or timeout → `unavailable`, never favourable |
| `lookup_purchase_history` | purchase_history.csv | lookup | none → empty list |

Every tool returns `{tool, ok, data, error, references}`. The `references` field (e.g. `SW003`, `V005`, `PO-2531`, `department_budgets:Marketing`) lists the only IDs that evidence is allowed to cite.

## Deterministic vs. model-driven

| Decided by code | Decided by the model |
|---|---|
| Budget sufficient / shortfall (§2) | Is there a credible gap vs. an overlapping tool? (§3) |
| Financial approval tier (§4) | Does an AI tool fit the requested data class? (§8) |
| Missing required fields (§1) | Which next action, among those the guardrails allow |
| Security triggers: sensitive data or integrations, assessment missing / not completed / older than 365 days from 2026-09-30 (§5) | Recommendation and next-step wording |
| Registry vs. vendor-risk API conflict (§5) | Key points citing evidence IDs |
| Privacy triggers: PII, data stored outside the region (§6) | Extra flags or approvals, with a reason (it can add, never remove) |
| Legal triggers: new vendor with spend ≥ $10k, terms not approved, cross-region PII (§7) | A second layer of injection detection |
| Injection detection (§9), tool-failure handling (§10), human review always on (§11) | |

## Agent responsibilities

- **A · Single agent:** one call. It reads the request, the evidence lines and the rules output, plus policy §3, §8 and §9 (the sections that need judgment). It returns an `LLMAssessment`.
- **B · Analyst:** turns the same input into a short structured `EvidencePack`: facts with references, overlap analysis, open questions, concerns and a draft action.
- **B · Policy/Risk Reviewer:** reads the pack *and* the original evidence and rules output, checks the analyst's work, and returns the same `LLMAssessment` plus `corrections_to_analyst`.

## Handoff format

- **Into the model.** Request and evidence go inside `<untrusted_business_data>` tags (data, never instructions). The rules output goes inside `<trusted_rules_output>` as compact JSON: required approvals, risk flags, missing information, default action and key facts.
- **Analyst → Reviewer (B).** An `EvidencePack` JSON validated with Pydantic.
- **Model → code.** An `LLMAssessment` JSON in Groq JSON mode, validated with Pydantic, with one repair retry:
  ```json
  {"recommended_action": "proceed_to_approval | route_for_review | request_clarification | reuse_existing_tool",
   "recommendation": "...", "next_step": "...", "overlap_assessment": "... | null",
   "key_points": [{"finding": "...", "reference": "SW003"}],
   "additional_risk_flags": [{"name": "...", "reason": "..."}],
   "additional_approvals": [{"name": "...", "reason": "..."}],
   "injection_suspected": false}
  ```
- **Code → UI and evaluation.** The `ProcurementDecision` contract, unchanged, plus the optional fields `recommended_action` and `telemetry` (calls, tokens, latency, rate-limit wait, failed tools, dropped claims).

## Stop / escalation conditions

| Condition | Effect |
|---|---|
| Material information missing | Action forced to `request_clarification`; the model cannot override |
| Any specialist flag (budget, security, privacy, legal, conflict, unavailable, expired) | Model cannot choose `proceed_to_approval`; becomes `route_for_review` |
| Model says `reuse_existing_tool` but code found no overlapping product | Reverts to the deterministic default action |
| Vendor-risk API down or unknown | `vendor_risk_unavailable` + Security review; status never assumed favourable |
| Registry and API disagree | `conflicting_vendor_evidence` + Security; the conflict is shown, not resolved |
| Injection text found | Flag shown, text ignored, full policy still applied |
| Model claim cites an ID no tool returned | Claim dropped and counted in telemetry |
| LLM error: invalid output, outage, daily quota, request too large, rate-limit wait over the limit (20 s UI / 120 s batch) | Rules-only decision returned immediately, flagged `llm_unavailable` |
| Every outcome | `human_review_required = true`; a human records the final decision |

## What I intentionally did not build

- **LLM-driven tool calling.** Every request needs every check, so letting the model pick tools adds latency and a way to skip a check, with no benefit.
- **More than two agents.** Two agents already cost twice as much as one without better results (see the memo).
- **Automatic purchasing, approval or budget changes.** These are forbidden by policy §11.
- **Seat-utilisation analysis.** There's no utilisation data, so overlap with possibly unused capacity is flagged for a human instead.
- **Authentication, a database or multi-user workflow.** Decisions go to a local JSONL log.
- **Resolving conflicts between vendor sources automatically.** The policy says to surface conflicts, not pick a side.
