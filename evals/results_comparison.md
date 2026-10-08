# Architecture comparison results

Generated 2026-10-08 18:13 - 16 labelled cases; LLM architectures x 2 repeat(s), rules_only x 1 (deterministic).
Model: `qwen/qwen3.8-27b` (Groq) - live model calls, recorded to evals/llm_cache.jsonl. Single repeat 1 and staged were recorded before the L-10 reuse guardrail was added; single repeat 2 after it.
Latency excl. wait = end-to-end minus provider rate-limit backoff (free tier: 8,000 tokens/minute).

| Metric | rules_only | single | staged |
|---|---:|---:|---:|
| All rubric checks passed | 94% | 91% | 88% |
| Correct next action | 94% | 91% | 88% |
| Policy followed (approvals/flags/missing exact) | 100% | 100% | 100% |
| Human escalation correct | 100% | 100% | 100% |
| Under-escalated runs (dangerous direction) | 0 | 0 | 0 |
| Evidence grounded | 100% | 100% | 100% |
| Ungrounded model claims dropped (total) | 0 | 8 | 5 |
| LLM errors / fallbacks | 0 | 0 | 0 |
| Avg latency, end-to-end (ms) | 153 | 21,152 | 56,610 |
| Avg latency excl. rate-limit wait (ms) | 153 | 1,518 | 3,880 |
| p95 latency excl. wait (ms) | 2,036 | 3,488 | 10,957 |
| Avg LLM calls | 0.0 | 1.0 | 2.0 |
| Avg tool calls | 7.0 | 7.0 | 7.0 |
| Avg tokens (prompt + completion) | 0 | 2,109 | 4,621 |
| Cases with identical output across repeats | deterministic | 15/16 | n/a (1 repeat) |

## Failures

- **rules_only** L-08 (repeat 1): action route_for_review not in ['reuse_existing_tool']
- **single** L-08 (repeat 1): action route_for_review not in ['reuse_existing_tool']
- **single** L-10 (repeat 1): action reuse_existing_tool not in ['proceed_to_approval']
- **staged** L-08 (repeat 1): action route_for_review not in ['reuse_existing_tool']
- **staged** L-10 (repeat 1): action route_for_review not in ['proceed_to_approval']
- **single** L-08 (repeat 2): action route_for_review not in ['reuse_existing_tool']
