# Architecture Decision Memo

**Maximum length: 500 words**

## Decision
I recommend shipping the **single agent** architecture.

## Evidence
Both architectures were evaluated against the 6 public edge cases, which included complex scenarios like budget shortfalls, API outages, and malicious prompt injections.

| Metric | Single agent | Staged / 2-agent |
|---|---:|---:|
| Cases passing your quality criteria | 6/6 | 6/6 |
| Avg latency | ~3 - 10s | ~30 - 60s |
| Avg LLM calls | 1 | 2 |
| Avg tool calls | 6 (deterministic) | 6 (deterministic) |
| Notable policy/grounding failures | 0 | 0 |

## Trade-offs
**What improved with Staged?** 
The staged architecture logically separated the tasks (summarizing raw data vs. applying policy), making the final context window for the Reviewer agent slightly cleaner.

**What became slower/more expensive?** 
The staged approach doubled our LLM invocations and token usage per request. Because of the synthetic rate limits on our chosen model (`qwen3.8-27b`), making sequential LLM calls caused severe throttling. We were forced to introduce 15-second backoffs, resulting in abysmal user latency (upwards of 60 seconds) for the staged variant, compared to the single agent which frequently finished in under 5 seconds.

## Risks / limitations
Before rolling this into production, I would validate:
1. **JSON Output Stability:** We are currently using regex and string manipulation to strip `<think>` tags and format the LLM's JSON. In production, we should enforce strict structured outputs using standard tools (like OpenAI's structured JSON schema or Pydantic's `instructor` library) to ensure 100% parseable API responses.
2. **Context Window Limits:** Currently, we pass the entire policy text and full software catalog overlap results into the system prompt. If the catalog grows massive, we risk blowing out the context window. 

## Why this is the right MVP
The assignment's core design principle is "CODE for deterministic checks; AI for context." 

Many AI implementations fail because they rely on fragile LLM tool-calling loops (like ReAct) to execute basic queries. By pushing all 6 tool calls (budget lookup, catalog search, API calls) into deterministic Python code *before* the LLM is even invoked, we eliminated tool-hallucinations entirely. 

The single-agent architecture receives a perfectly structured dictionary of evidence and simply acts as a reasoning engine to apply the policy rules. This approach is highly reliable, easily testable, significantly cheaper, and performs as well as the more complex 2-agent setup. Unnecessary orchestration is a liability in production; our single-agent MVP solves the exact business problem with maximum efficiency.
