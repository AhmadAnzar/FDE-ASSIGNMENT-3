# Architecture comparison results

Generated 2026-10-08 12:42 - 16 labelled cases; LLM architectures x 1 repeat(s), rules_only x 1 (deterministic).
Model: `qwen/qwen3.8-27b` (Groq) - replayed from recorded model responses (evals/llm_cache.jsonl); latency and tokens are as recorded.
Latency excl. wait = end-to-end minus provider rate-limit backoff (free tier: 8,000 tokens/minute).

| Metric | rules_only | single | staged |
|---|---:|---:|---:|
| All rubric checks passed | 94% | 94% | 88% |
| Correct next action | 94% | 94% | 88% |
| Policy followed (approvals/flags/missing exact) | 100% | 100% | 100% |
| Human escalation correct | 100% | 100% | 100% |
| Evidence grounded | 100% | 100% | 100% |
| Ungrounded model claims dropped (total) | 0 | 4 | 5 |
| LLM errors / fallbacks | 0 | 0 | 0 |
| Avg latency, end-to-end (ms) | 156 | 21,187 | 56,465 |
| Avg latency excl. rate-limit wait (ms) | 156 | 1,514 | 3,735 |
| p95 latency excl. wait (ms) | 2,076 | 3,866 | 10,834 |
| Avg LLM calls | 0.0 | 1.0 | 2.0 |
| Avg tool calls | 7.0 | 7.0 | 7.0 |
| Avg tokens (prompt + completion) | 0 | 2,109 | 4,621 |
| Cases with identical output across repeats | deterministic | n/a (1 repeat) | n/a (1 repeat) |

## Failures

- **rules_only** L-08 (repeat 1): action route_for_review not in ['reuse_existing_tool']
- **single** L-08 (repeat 1): action route_for_review not in ['reuse_existing_tool']
- **staged** L-08 (repeat 1): action route_for_review not in ['reuse_existing_tool']
- **staged** L-10 (repeat 1): action route_for_review not in ['proceed_to_approval']
