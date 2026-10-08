# Architecture comparison results

Generated 2026-10-08 15:45 - 6 labelled cases; LLM architectures x 1 repeat(s), rules_only x 1 (deterministic).
Model: `qwen/qwen3.8-27b` (Groq) - live model calls, PROMPT_VARIANT=full (original prompt: full policy + raw tool JSON).
Latency excl. wait = end-to-end minus provider rate-limit backoff (free tier: 8,000 tokens/minute).

| Metric | single |
|---|---:|
| All rubric checks passed | 100% |
| Correct next action | 100% |
| Policy followed (approvals/flags/missing exact) | 100% |
| Human escalation correct | 100% |
| Under-escalated runs (dangerous direction) | 0 |
| Evidence grounded | 100% |
| Ungrounded model claims dropped (total) | 1 |
| LLM errors / fallbacks | 0 |
| Avg latency, end-to-end (ms) | 25,468 |
| Avg latency excl. rate-limit wait (ms) | 1,428 |
| p95 latency excl. wait (ms) | 1,745 |
| Avg LLM calls | 1.0 |
| Avg tool calls | 7.0 |
| Avg tokens (prompt + completion) | 3,650 |
| Cases with identical output across repeats | n/a (1 repeat) |

## Failures

None.
