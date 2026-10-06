# AI Procurement Request Copilot

This repository contains my solution for the AI Procurement Request Copilot. The goal of this product is to assist procurement analysts by automatically gathering evidence for new software requests, checking them against company policies, and generating structured recommendations while keeping final decisions in the hands of human reviewers.

## Setup & Run Instructions

**Prerequisites:** Python 3.11+ and a Groq API Key.

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
2. **Configure your API Key:**
   Create a `.env` file in the root directory and add your Groq API key:
   ```env
   GROQ_API_KEY=your_key_here
   ```
3. **Start the Mock Vendor API and Local UI:**
   ```bash
   python run_local.py
   ```
   *Note: Leave this terminal running so the mock vendor API is available on port 8001.*
4. **Run the Evaluation Harness:**
   In a separate terminal, run:
   ```bash
   python evals/run_public_evals.py --architecture single
   python evals/run_public_evals.py --architecture staged
   ```

## Product Workflow & Architecture

Instead of relying on fragile LLM tool-calling loops that often lead to hallucinations, infinite loops, and high latency, I designed the system around the principle of **"Code for deterministic checks, AI for contextual reasoning."**

### Tools (Evidence Gathering)
All tools are executed deterministically in Python *before* the LLM is invoked:
1. **`check_budget` (Deterministic):** Checks the requested cost against the `department_budgets.csv`.
2. **`calculate_approval_thresholds` (Deterministic):** Calculates the base required approvals based on cost.
3. **`check_software_catalog` (Data):** Finds overlapping software by vendor, product name, or category.
4. **`check_internal_vendor_registry` (Data):** Looks up the vendor's approved status internally.
5. **`check_vendor_risk_api` (API):** Calls the mock vendor API (port 8001) for external risk status.
6. **`employee_lookup` (Data):** Gets the requester's department and level.

### Architectures Evaluated
*   **Architecture A (Single Agent):** Python gathers all evidence into a structured dictionary. The `qwen/qwen3.8-27b` model is then called exactly once to review the evidence against the policy and output a final JSON decision.
*   **Architecture B (Staged / 2-Agent):** Python gathers all evidence. Agent 1 (Analyst) summarizes the raw data into an Evidence Pack. Agent 2 (Reviewer) reads the pack and the policy to output the final JSON decision.

## Evaluation Results

| Metric | Single Agent | Staged (2-Agent) |
|---|---|---|
| **Public Cases Passed** | 6/6 | 6/6 |
| **Average Latency** | ~3 - 10 seconds | ~30 - 60 seconds (rate limited) |
| **LLM Calls per Request**| 1 | 2 |
| **Tool Calls per Request**| 6 (Deterministic) | 6 (Deterministic) |
| **Prompt Injection** | Handled correctly | Handled correctly |

## Final Ship Decision

I am shipping **Architecture A (Single Agent)**. 

By aggressively front-loading the data retrieval using standard Python logic, the single agent has perfect context without needing to execute a complex ReAct loop. Both architectures achieved a 100% pass rate on edge cases (including catching the prompt injection in PUB-05 and handling the API failure in PUB-06). However, the Staged architecture unnecessarily doubles the token cost and consistently hits API rate limits (causing severe latency spikes). The single agent is cheaper, faster, and perfectly solves the business problem.

## Known Limitations & Assumptions
*   **Assumptions:** I assumed the mock Vendor Risk API will always run on `localhost:8001`. I also assumed that the "cost" is annual, and if a cost is missing, we must flag it rather than rejecting it outright.
*   **Limitations:** The current LLM (`qwen3.8-27b`) was chosen due to mock environment constraints. In a real-world scenario, a model with stronger native JSON instruction following (like `gpt-4o`) would remove the need for regex fallback parsing on the JSON output. Additionally, the system currently pulls the full software catalog into memory, which would need pagination for an enterprise-scale database.
