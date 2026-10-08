# AI Procurement Request Copilot

An internal tool that gathers evidence for a software purchase request, applies the procurement policy and recommends the next action. It never approves anything: every outcome ends with a human decision.

**Design principle:** code runs the checks and the AI makes the judgment calls. Humans keep the approvals.

- **Code** gathers all evidence with 7 tools and applies every policy rule that can be written as an `if`.
- **The LLM** handles what needs judgment:
  - does an existing tool already cover the need?
  - does the AI tool fit the data class?
  - what should happen next, and how to explain it
- **Humans** approve spend, legal terms and exceptions in the UI.

## Quick start

Requires Python 3.11+ and a [Groq](https://console.groq.com) API key.

```bash
pip install -r requirements.txt
cp .env.example .env            # then set GROQ_API_KEY
python run_local.py             # mock vendor-risk API (:8001) + UI (http://localhost:8501)
```

Run the evaluation, tests and pre-flight check:

```bash
python evals/run_comparison.py --replay    # full architecture comparison in seconds, no API key
python evals/run_comparison.py --quick     # live: rules-only + single agent
python -m unittest discover -s tests        # 33 unit tests, no LLM or server needed
python verify_setup.py                      # starter-pack pre-flight
```

## How it works

```
request → [code] 7 tools → [code] injection guard → [code] rules engine ─┐
                                                                         ├→ [code] merge + guardrails → UI → human decision
                                     [LLM] single agent (A)  or  ────────┘
                                     [LLM] analyst → reviewer (B)
```

See **[docs/architecture.md](docs/architecture.md)** and **[templates/workflow_notes.md](templates/workflow_notes.md)** for the diagram, the tool table, the rule-by-rule policy mapping, the handoff formats, stop/escalation conditions, assumptions and what was deliberately not built.

- **Tools** (`src/tools.py`): employee, budget (deterministic), approval thresholds (deterministic), catalog, vendor registry, vendor-risk API, purchase history. They never raise. A failed tool returns an explicit status, which the rules treat as "not verified", never as favourable.
- **Rules engine** (`src/rules.py`): computes the required approvals, risk flags and missing information for policy §1–§10, using the 2026-09-30 reference date. Every evidence item cites a real record ID (`SW003`, `V005`, `PO-2531`, …) or a policy section.
- **Injection guard** (`src/guard.py`): scans the request text and the tool `notes` fields. It *flags* injection attempts and continues; it never short-circuits (policy §9).
- **Agents** (`src/agents.py`): the LLM sees the request and evidence as `<untrusted_business_data>` and the rules output as trusted. It returns a Pydantic-validated `LLMAssessment`.
- **Merge** (`src/solution.py`):
  - the LLM can **add** approvals and flags but never remove them
  - missing information forces "request clarification"
  - specialist flags block "proceed"
  - claims citing unknown records are dropped
  - `human_review_required` is always `true`
  - if the LLM fails (outage, invalid output, quota), the rules-only decision is returned and flagged `llm_unavailable`

## Evaluation

See `evals/README.md`. There are 16 labelled cases, all run identically on every architecture:
- all 10 requests in the dataset
- 5 synthetic requests (CFO tier, injection-guard false-positive check, reworded injection, unknown requester with an unregistered vendor, employee PII)
- one vendor-risk outage scenario

The rubric matches the brief: correct next action, policy followed, escalation correct, evidence grounded, plus latency, LLM/tool calls and tokens.

### Results

Current code, replayed from the recorded run (`python evals/run_comparison.py --replay`, about 15 s, no API key):

| Metric | Rules only (no AI) | **A · Single** | B · Analyst → Reviewer |
|---|---:|---:|---:|
| All rubric checks passed | 15/16 | **15/16** | 14/16 |
| Policy followed / escalation correct / evidence grounded | 100% | 100% | 100% |
| Model latency (excl. rate-limit wait) | 0.2 s | **1.5 s** | 3.7 s |
| End-to-end latency on Groq free tier | 0.2 s | 21 s | 56 s |
| LLM calls / tokens per request | 0 / 0 | **1 / 2.1k** | 2 / 4.6k |
| Ungrounded model claims blocked | - | 4 | 5 |

- **Remaining failures:**
  - **L-08**, all three variants: they route an obvious duplicate to review instead of recommending reuse. This is conservative, and the overlap stays visible to the human.
  - **L-10**, staged only: it over-escalates a $950 training pack.
- **Raw live run** (before the L-10 reuse guardrail was added): `evals/results_comparison.md`. Single scored 14/16 there.
- **Prompt trim** (`evals/results_prompt_full.md`, 6 public cases, single agent): prompt tokens went from 3,234 to 1,708 (−47%) and model latency from 1.43 s to 1.20 s, with the same 6/6 pass rate.

## Ship decision

**Ship A: the single agent on the deterministic rules engine.** Staged matched or trailed it on every quality metric while costing about 2× the tokens and model time. Full reasoning is in **[templates/architecture_decision.md](templates/architecture_decision.md)** (≤ 500 words).

## Assumptions & limitations

- `annual_cost_usd` is the annualised amount. Review currency is measured against the policy snapshot date 2026-09-30.
- Missing cost leaves the approval tier undetermined. The system does not fall back to the lowest tier.
- An unknown data-access level triggers Security review. It also triggers Privacy when the vendor stores data outside the region.
- There is no seat-utilisation data, so overlap with unused capacity is flagged for a human to confirm.
- The Groq free tier is limited to 8k tokens/minute and 200k tokens/day per organisation. Batch evaluation is therefore throttled by the provider, not the pipeline. `--replay` reproduces recorded runs without calling the API.
- Human decisions are written to a local `decisions_log.jsonl`. There is no authentication or database.
