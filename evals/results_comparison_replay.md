# Architecture comparison results

Generated 2026-10-08 18:13 - 16 labelled cases; LLM architectures x 2 repeat(s), rules_only x 1 (deterministic).
Model: `qwen/qwen3.8-27b` (Groq) - replayed from recorded model responses (evals/llm_cache.jsonl); latency and tokens are as recorded.
Latency excl. wait = end-to-end minus provider rate-limit backoff (free tier: 8,000 tokens/minute).

| Metric | rules_only | single | staged |
|---|---:|---:|---:|
| All rubric checks passed | 94% | 94% | 88% |
| Correct next action | 94% | 94% | 88% |
| Policy followed (approvals/flags/missing exact) | 100% | 100% | 100% |
| Human escalation correct | 100% | 100% | 100% |
| Under-escalated runs (dangerous direction) | 0 | 0 | 0 |
| Evidence grounded | 100% | 100% | 100% |
| Ungrounded model claims dropped (total) | 0 | 8 | 5 |
| LLM errors / fallbacks | 0 | 0 | 0 |
| Avg latency, end-to-end (ms) | 154 | 21,043 | 56,458 |
| Avg latency excl. rate-limit wait (ms) | 154 | 1,410 | 3,728 |
| p95 latency excl. wait (ms) | 2,049 | 3,292 | 10,820 |
| Avg LLM calls | 0.0 | 1.0 | 2.0 |
| Avg tool calls | 7.0 | 7.0 | 7.0 |
| Avg tokens (prompt + completion) | 0 | 2,109 | 4,621 |
| Cases with identical output across repeats | deterministic | 16/16 | n/a (1 repeat) |

## Failures

- **rules_only** L-08 (repeat 1): action route_for_review not in ['reuse_existing_tool']
- **single** L-08 (repeat 1): action route_for_review not in ['reuse_existing_tool']
- **single** L-08 (repeat 2): action route_for_review not in ['reuse_existing_tool']
- **staged** L-08 (repeat 1): action route_for_review not in ['reuse_existing_tool']
- **staged** L-10 (repeat 1): action route_for_review not in ['proceed_to_approval']
