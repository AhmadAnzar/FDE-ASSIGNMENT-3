# Architecture Decision Memo

## Decision
Ship **Architecture A: a single agent on a deterministic rules engine.** If the model is unavailable, the system falls back to the rules-only decision.

## Evidence
16 labelled cases, the same for every architecture (10 dataset, 5 synthetic, 1 API outage). Model: `qwen/qwen3.8-27b` (Groq), 1 run per case. Current code, replaying the recorded run; the original live run is in `evals/results_comparison.md`.

| Metric | Rules only | **A · Single** | B · Staged |
|---|---:|---:|---:|
| All rubric checks passed | 15/16 | **15/16** | 14/16 |
| Policy / escalation / grounding | 100% | 100% | 100% |
| Model latency (excl. throttling) | 0.2 s | **1.5 s** | 3.7 s |
| End-to-end, free tier | 0.2 s | 21 s | 56 s |
| LLM calls / tokens | 0 / 0 | **1 / 2.1k** | 2 / 4.6k |
| Ungrounded claims blocked | - | 4 | 5 |

## Trade-offs
**Staged costs more and improves nothing.** The Reviewer saw the raw evidence, yet fixed none of the single agent's misses and over-escalated a $950 training pack, while doubling tokens and calls.

**The rules engine does the policy work.** Approvals, flags, missing information and escalation were 100% correct in all three variants, because code computes them and the model can only add to them. Budget, thresholds, review dates, conflicts, injection and tool failures are code, not prompt instructions.

**The AI does judgment and explanation.** It decides whether an existing tool covers the need and whether an AI tool fits the data class, and it writes a recommendation grounded in record IDs. Rules-only matches its next-action accuracy but cannot explain or judge credible gaps. Claims citing records no tool returned are removed.

**Judgment errors:**
- **L-10:** in the live run the model recommended "reuse" with nothing to reuse. I added a guardrail requiring a catalog overlap. Replaying the recorded answers confirms the fix (14 → 15/16) with no new model calls. This was a post-hoc fix, disclosed here.
- **L-08:** all variants routed a clear duplicate (TaskFlow Pro) to review instead of recommending reuse. That's conservative, and the overlap stays visible to the human.

**Prompt.** Using grounded evidence lines plus policy §3/§8/§9, instead of the full policy and raw tool JSON, cut prompt tokens by 47% with the same 6/6 on the public cases.

## Risks / limitations
- One run per case, so run-to-run variance is unmeasured; L-08 flipped between reuse and review in live use. I'd run 3–5 repeats before production.
- Small test set, with labels I wrote from the policy before any model run.

## Why this is the right MVP
One model call, deterministic policy and guardrails the model cannot override. When the AI is down, it degrades to a correct rules-only decision in under a second. Staged orchestration cost twice as much without better results, so the simpler system ships.
