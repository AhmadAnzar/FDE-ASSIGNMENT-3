# Evaluation

Two harnesses. Both need the mock vendor-risk API running (`python run_local.py`, or
`python -m uvicorn mock_api.app:app --port 8001`).

## 1. Architecture comparison (primary)

```bash
python evals/run_comparison.py --replay        # seconds, no API key: re-run on recorded model answers
python evals/run_comparison.py --quick         # live: rules_only + single, 1 repeat
python evals/run_comparison.py --record        # live: all architectures, records answers for --replay
python evals/run_comparison.py --record --append --architectures staged   # re-run one architecture, keep the rest
python evals/run_comparison.py --architectures single --repeats 1 --cases L-06 S-03
```

**Replay.** `--record` saves every model response, with its measured latency and token counts, to `llm_cache.jsonl`. `--replay` re-runs the real tools, rules engine, guardrails, merge and scoring, and serves only the model calls from that file. The whole comparison reproduces in seconds without an API key. Reported latency and tokens are the recorded ones. If a prompt, the policy excerpt or the model has changed since recording, replay stops with `ReplayMiss` rather than reporting stale numbers. Replay also re-scores recorded answers after a code-side change, such as a new guardrail, without spending any quota.

**Provider limits.** Live runs are bounded by the provider, not the pipeline. The Groq free tier for `qwen/qwen3.8-27b` allows 8,000 input tokens/minute, 1,000 output tokens/minute and 200,000 tokens/day per organisation. Model time per call is about 1–1.5 s; the rest of each live latency figure is rate-limit waiting, which the results report separately.

- **Test set** - `labeled_cases.json`: 16 cases, the same for every architecture.
  - `L-01..L-10`: all 10 requests in `data/requests.json`.
  - `S-01..S-05`: synthetic requests passed inline (the hidden set uses different records). They cover the CFO tier, a false-positive check for the injection guard, a reworded injection, an unknown requester with an unregistered vendor, and employee PII on an already-approved vendor.
  - `S-06`: REQ-1003 with the vendor-risk service unreachable.
- **Labels** - per case:
  - acceptable next action(s)
  - required and optional approvals
  - required and forbidden risk flags
  - expected missing-information items

  Labels were written from the policy, before the LLM runs. The only case with two accepted actions for a policy reason is L-01: policy §3 asks reviewers to check unused capacity, and the dataset has no seat-usage data.
- **Rubric** - matches the columns in `templates/evaluation_results_template.csv`:

  | Column | Passes when |
  |---|---|
  | `correct_next_action` | `recommended_action` is one of the labelled actions |
  | `policy_followed` | required ⊆ approvals ⊆ required + optional, required flags present, no forbidden flags, missing info correct |
  | `human_escalation_correct` | human review kept and specialist approvals (Finance/CFO/Security/Privacy/Legal) exactly as labelled |
  | `grounded_evidence` | every evidence reference resolves to a record ID a tool returned (computed independently) or a policy section |

- **Also recorded** - latency with and without provider rate-limit backoff, LLM/tool calls, tokens, ungrounded model claims dropped, LLM errors, and output stability across repeats.
- **`rules_only`** is a reference baseline with no LLM. It shows exactly what the LLM adds.
- **Outputs** - `results_comparison.csv` (one row per run) and `results_comparison.md` (summary + failures).

## 2. Public starter harness

```bash
python evals/run_public_evals.py --architecture single
python evals/run_public_evals.py --architecture staged
```

This runs the six provided public cases with their lenient minimum checks (substring matching) and writes `results_<architecture>.csv`. It is kept for compatibility; it is not the basis of the architecture decision.
