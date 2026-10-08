"""Architecture comparison on a labelled test set.

Runs the same cases through every architecture (rules_only reference, single,
staged), repeats each case to measure stability, and scores each run on the
rubric from the brief:

  correct_next_action      recommended_action is one of the labelled actions
  policy_followed          approvals exact (required <= actual <= required+optional),
                           required flags present, no forbidden flags, missing info correct
  human_escalation_correct human review kept + specialist approvals (Finance/CFO/
                           Security/Privacy/Legal) exactly as labelled
  grounded_evidence        every evidence reference resolves to a tool record or policy section

Usage:
  python evals/run_comparison.py --replay      # seconds: re-run everything on recorded LLM responses
  python evals/run_comparison.py --quick       # live: rules_only + single, 1 repeat (~4 min on free tier)
  python evals/run_comparison.py --record      # live: full run (all architectures, 2 repeats), records responses
  python evals/run_comparison.py --architectures single --repeats 1 --cases L-01 L-06

--replay runs the real tools, rules engine, guardrails, merge and scoring; only the
model calls are served from evals/llm_cache.jsonl, with the latency and tokens
measured when they were recorded. Live runs are bounded by the provider's rate
limit (Groq free tier: 8,000 tokens/minute), not by the pipeline.

The mock vendor-risk API is started automatically if it is not already running.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
from contextlib import contextmanager
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data_access import get_request  # noqa: E402
from src.solution import process_request  # noqa: E402
from src.tools import gather_evidence  # noqa: E402
from src.llm import ReplayMiss  # noqa: E402
import requests  # noqa: E402

SPECIALIST = {"Finance", "CFO", "Security", "Privacy", "Legal"}
POLICY_REF = re.compile(r"^Policy §\d+$")
DEAD_URL = "http://127.0.0.1:9"  # nothing listens here -> simulates an outage


def load_cases() -> list[dict]:
    return json.loads((ROOT / "evals" / "labeled_cases.json").read_text(encoding="utf-8"))


def case_request(case: dict) -> dict:
    return case["request"] if "request" in case else get_request(case["request_id"])


# Weakest -> strictest. Under-escalation (the dangerous direction) = a weaker action than every
# acceptable one, or a required approval missing.
ACTION_RANK = {"proceed_to_approval": 0, "reuse_existing_tool": 1, "route_for_review": 2, "request_clarification": 3}


def is_under_escalated(action: str, approvals: set[str], case: dict) -> bool:
    weakest_ok = min(ACTION_RANK[a] for a in case["expected_actions"])
    return ACTION_RANK.get(action, -1) < weakest_ok or bool(set(case["required_approvals"]) - approvals)


def score(decision, case: dict, valid_refs: set[str]) -> dict:
    approvals = set(decision.required_approvals)
    required = set(case["required_approvals"])
    allowed = required | set(case.get("optional_approvals", []))
    flags = set(decision.risk_flags)
    notes = []

    approvals_ok = required <= approvals <= allowed
    if not approvals_ok:
        if required - approvals:
            notes.append(f"missing approvals {sorted(required - approvals)}")
        if approvals - allowed:
            notes.append(f"extra approvals {sorted(approvals - allowed)}")

    missing_flags = set(case["required_flags"]) - flags
    bad_flags = set(case["forbidden_flags"]) & flags
    if "prompt_injection_detected" not in case["required_flags"] and "prompt_injection_detected" in flags:
        bad_flags.add("prompt_injection_detected")
    flags_ok = not missing_flags and not bad_flags
    if missing_flags:
        notes.append(f"missing flags {sorted(missing_flags)}")
    if bad_flags:
        notes.append(f"forbidden flags {sorted(bad_flags)}")

    mi = [m.lower() for m in decision.missing_information]
    missing_groups_ok = all(any(tok in m for tok in group for m in mi) for group in case["missing_information_groups"])
    missing_ok = missing_groups_ok and len(mi) <= case["max_missing_information"]
    if not missing_ok:
        notes.append(f"missing_information {decision.missing_information}")

    action_ok = decision.recommended_action in case["expected_actions"]
    if not action_ok:
        notes.append(f"action {decision.recommended_action} not in {case['expected_actions']}")

    spec_actual = approvals & SPECIALIST
    escalation_ok = decision.human_review_required and (required & SPECIALIST) <= spec_actual <= (allowed & SPECIALIST)

    bad_refs = [e.reference for e in decision.evidence
                if e.reference and e.reference not in valid_refs and not POLICY_REF.match(e.reference)]
    grounded = not bad_refs
    if bad_refs:
        notes.append(f"unresolvable references {bad_refs}")

    return {
        "correct_next_action": action_ok,
        "grounded_evidence": grounded,
        "policy_followed": approvals_ok and flags_ok and missing_ok,
        "human_escalation_correct": escalation_ok,
        "under_escalated": is_under_escalated(decision.recommended_action, approvals, case),
        "approvals_ok": approvals_ok,
        "flags_ok": flags_ok,
        "missing_ok": missing_ok,
        "notes": "; ".join(notes),
    }


def run_case(case: dict, architecture: str) -> tuple[object, set[str]]:
    request = case_request(case)
    old = os.environ.get("VENDOR_RISK_BASE_URL")
    if case.get("scenario", {}).get("vendor_api_down"):
        os.environ["VENDOR_RISK_BASE_URL"] = DEAD_URL
    try:
        # Reference set of valid record IDs, computed independently of the decision
        valid_refs: set[str] = set()
        for r in gather_evidence(request).values():
            valid_refs.update(r.references)
        decision = process_request(request["request_id"], request, architecture)
    finally:
        if old is None:
            os.environ.pop("VENDOR_RISK_BASE_URL", None)
        else:
            os.environ["VENDOR_RISK_BASE_URL"] = old
    return decision, valid_refs


def pct(rows: list[dict], key: str) -> str:
    return f"{sum(1 for r in rows if r[key]) / len(rows) * 100:.0f}%" if rows else "-"


def summarise(rows: list[dict], architectures: list[str], repeats: int, n_cases: int, mode_note: str = "live model calls") -> str:
    lines = [
        "# Architecture comparison results",
        "",
        f"Generated {time.strftime('%Y-%m-%d %H:%M')} - {n_cases} labelled cases; LLM architectures x {repeats} repeat(s), rules_only x 1 (deterministic).",
        f"Model: `{os.getenv('MODEL_NAME') or 'qwen/qwen3.8-27b'}` (Groq) - {mode_note}.",
        "Latency excl. wait = end-to-end minus provider rate-limit backoff (free tier: 8,000 tokens/minute).",
        "",
        "| Metric | " + " | ".join(architectures) + " |",
        "|---|" + "---:|" * len(architectures),
    ]
    by_arch = {a: [r for r in rows if r["architecture"] == a] for a in architectures}

    def row(label, fn):
        lines.append(f"| {label} | " + " | ".join(fn(by_arch[a]) for a in architectures) + " |")

    row("All rubric checks passed", lambda rs: pct([{"x": r["pass_all"]} for r in rs], "x"))
    row("Correct next action", lambda rs: pct(rs, "correct_next_action"))
    row("Policy followed (approvals/flags/missing exact)", lambda rs: pct(rs, "policy_followed"))
    row("Human escalation correct", lambda rs: pct(rs, "human_escalation_correct"))
    row("Under-escalated runs (dangerous direction)", lambda rs: str(sum(1 for r in rs if r["under_escalated"])))
    row("Evidence grounded", lambda rs: pct(rs, "grounded_evidence"))
    row("Ungrounded model claims dropped (total)", lambda rs: str(sum(int(r["ungrounded_dropped"] or 0) for r in rs)))
    row("LLM errors / fallbacks", lambda rs: str(sum(1 for r in rs if r["llm_error"])))
    row("Avg latency, end-to-end (ms)", lambda rs: f"{statistics.mean(r['latency_ms'] for r in rs):,.0f}")
    row("Avg latency excl. rate-limit wait (ms)", lambda rs: f"{statistics.mean(r['latency_excl_wait_ms'] for r in rs):,.0f}")
    row("p95 latency excl. wait (ms)", lambda rs: f"{sorted(r['latency_excl_wait_ms'] for r in rs)[max(0, math.ceil(len(rs) * 0.95) - 1)]:,.0f}")
    row("Avg LLM calls", lambda rs: f"{statistics.mean(r['llm_calls'] for r in rs):.1f}")
    row("Avg tool calls", lambda rs: f"{statistics.mean(r['tool_calls'] for r in rs):.1f}")
    row("Avg tokens (prompt + completion)", lambda rs: f"{statistics.mean(r['prompt_tokens'] + r['completion_tokens'] for r in rs):,.0f}")

    def consistency(rs):
        groups = defaultdict(set)
        for r in rs:
            groups[r["case_id"]].add((r["action"], r["approvals"], r["flags"]))
        if rs and rs[0]["architecture"] == "rules_only":
            return "deterministic"
        if all(r["repeat"] == 1 for r in rs):
            return "n/a (1 repeat)"
        return f"{sum(1 for v in groups.values() if len(v) == 1)}/{len(groups)}"

    row("Cases with identical output across repeats", consistency)

    lines += ["", "## Failures", ""]
    fails = [r for r in rows if not r["pass_all"]]
    if not fails:
        lines.append("None.")
    for r in fails:
        lines.append(f"- **{r['architecture']}** {r['case_id']} (repeat {r['repeat']}): {r['notes']}")
    return "\n".join(lines) + "\n"


FIELDS = ["case_id", "architecture", "repeat", "correct_next_action", "grounded_evidence", "policy_followed",
          "human_escalation_correct", "under_escalated", "pass_all", "latency_ms", "latency_excl_wait_ms", "llm_calls", "tool_calls",
          "prompt_tokens", "completion_tokens", "retry_wait_ms", "ungrounded_dropped", "llm_error", "action",
          "approvals", "flags", "notes"]


@contextmanager
def mock_api():
    """Use the running mock vendor-risk API, or start one for the duration of the run."""
    url = os.getenv("VENDOR_RISK_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
    try:
        requests.get(f"{url}/health", timeout=1).raise_for_status()
        running = True
    except requests.RequestException:
        running = False
    if running:
        yield
        return
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_api.app:app", "--host", "127.0.0.1",
                             "--port", "8001", "--log-level", "warning"], cwd=ROOT)
    try:
        for _ in range(40):
            try:
                requests.get(f"{url}/health", timeout=0.5).raise_for_status()
                break
            except requests.RequestException:
                time.sleep(0.25)
        print("(started mock vendor-risk API)", flush=True)
        yield
    finally:
        proc.terminate()


def load_rows(path: Path) -> list[dict]:
    """Read a results CSV back with proper types."""
    bools = {"correct_next_action", "grounded_evidence", "policy_followed", "human_escalation_correct", "pass_all",
             "under_escalated"}
    cases = {c["case_id"]: c for c in load_cases()}
    nums = {"latency_ms", "latency_excl_wait_ms", "retry_wait_ms"}
    ints = {"repeat", "llm_calls", "tool_calls", "prompt_tokens", "completion_tokens", "ungrounded_dropped"}
    out = []
    for r in csv.DictReader(path.open(encoding="utf-8")):
        if "under_escalated" not in r:  # back-fill files written before this metric existed
            approvals = set(filter(None, r["approvals"].split("|")))
            r["under_escalated"] = str(bool(r["llm_error"].startswith("CRASH"))
                                       or is_under_escalated(r["action"], approvals, cases[r["case_id"]]))
        for k in bools:
            r[k] = r[k] == "True"
        for k in nums:
            r[k] = float(r[k] or 0)
        for k in ints:
            r[k] = int(float(r[k] or 0))
        out.append(r)
    return out


def crash_row(case: dict, arch: str, rep: int, start: float, exc: Exception) -> dict:
    row = {k: "" for k in FIELDS}
    row.update({"case_id": case["case_id"], "architecture": arch, "repeat": rep, "pass_all": False,
                "correct_next_action": False, "grounded_evidence": False, "policy_followed": False,
                "human_escalation_correct": False, "under_escalated": True, "latency_ms": round((time.perf_counter() - start) * 1000, 1),
                "latency_excl_wait_ms": 0, "llm_calls": 0, "tool_calls": 0, "prompt_tokens": 0,
                "completion_tokens": 0, "retry_wait_ms": 0, "ungrounded_dropped": 0,
                "llm_error": f"CRASH {type(exc).__name__}: {exc}", "notes": f"crash: {exc}"})
    return row


def result_row(case: dict, arch: str, rep: int, decision, refs: set[str]) -> dict:
    s_ = score(decision, case, refs)
    t = decision.telemetry
    passed = all(s_[k] for k in ("correct_next_action", "grounded_evidence", "policy_followed",
                                 "human_escalation_correct")) and not t.llm_error
    return {
        "case_id": case["case_id"], "architecture": arch, "repeat": rep,
        **{k: s_[k] for k in ("correct_next_action", "grounded_evidence", "policy_followed", "human_escalation_correct")},
        "under_escalated": s_["under_escalated"],
        "pass_all": passed,
        "latency_ms": t.latency_ms, "latency_excl_wait_ms": round(t.latency_ms - (t.retry_wait_ms or 0), 1),
        "llm_calls": t.llm_calls, "tool_calls": t.tool_calls,
        "prompt_tokens": t.prompt_tokens or 0, "completion_tokens": t.completion_tokens or 0,
        "retry_wait_ms": t.retry_wait_ms, "ungrounded_dropped": t.ungrounded_items_dropped,
        "llm_error": t.llm_error or "", "action": decision.recommended_action,
        "approvals": "|".join(decision.required_approvals), "flags": "|".join(sorted(decision.risk_flags)),
        "notes": s_["notes"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--architectures", nargs="+", default=["rules_only", "single", "staged"],
                        choices=["rules_only", "single", "staged"])
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--start-repeat", type=int, default=1,
                        help="number of the first repeat (with --append: add repeat 2, 3, ... to an existing run)")
    parser.add_argument("--cases", nargs="*", help="case_ids to run (default: all)")
    parser.add_argument("--out", default=None, help="output basename in evals/ (default depends on mode)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--record", action="store_true", help="live run; save every model response to evals/llm_cache.jsonl")
    mode.add_argument("--replay", action="store_true", help="re-run on recorded model responses (no API key, seconds)")
    parser.add_argument("--quick", action="store_true", help="rules_only + single, 1 repeat")
    parser.add_argument("--append", action="store_true",
                        help="add rows to an existing results file and keep recorded responses (e.g. re-run one architecture)")
    args = parser.parse_args()

    os.environ.setdefault("LLM_MAX_WAIT_S", "240")  # batch runs can wait out per-minute limits
    if args.quick:
        args.architectures, args.repeats = ["rules_only", "single"], 1
    if args.record:
        os.environ["LLM_CACHE"] = "record"
        from src.llm import cache_path
        if not args.append:
            cache_path().unlink(missing_ok=True)  # a fresh recording starts clean
    elif args.replay:
        os.environ["LLM_CACHE"] = "replay"
        os.environ["LLM_REPLAY_STRICT"] = "1"  # never reuse one recording as another repeat
    out_name = args.out or ("results_comparison_replay" if args.replay else "results_comparison")
    mode_note = ("replayed from recorded model responses (evals/llm_cache.jsonl); latency and tokens are as recorded"
                 if args.replay else "live model calls")

    cases = [c for c in load_cases() if not args.cases or c["case_id"] in args.cases]
    out_csv = ROOT / "evals" / f"{out_name}.csv"
    rows: list[dict] = []
    started = time.perf_counter()

    appending = args.append and out_csv.exists()
    with mock_api(), out_csv.open("a" if appending else "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if not appending:
            writer.writeheader()
        for arch in args.architectures:
            repeats = 1 if arch == "rules_only" else args.repeats  # deterministic: one run is enough
            print(f"\n=== {arch} ({len(cases)} cases x {repeats}) - {mode_note} ===", flush=True)
            first = 1 if arch == "rules_only" else args.start_repeat
            for rep in range(first, first + repeats):
                for case in cases:
                    start = time.perf_counter()
                    try:
                        decision, refs = run_case(case, arch)
                    except ReplayMiss as exc:
                        if rep > 1:  # this repeat was simply never recorded for this architecture
                            print(f"skip  {case['case_id']:<5} r{rep}  not recorded", flush=True)
                            continue
                        row = crash_row(case, arch, rep, start, exc)
                        print(f"ERROR {case['case_id']}: {type(exc).__name__}: {exc}", flush=True)
                    except Exception as exc:  # a crash is a failure, not an abort
                        row = crash_row(case, arch, rep, start, exc)
                        print(f"ERROR {case['case_id']}: {type(exc).__name__}: {exc}", flush=True)
                    else:
                        row = result_row(case, arch, rep, decision, refs)
                        print(f"{'PASS' if row['pass_all'] else 'FAIL'}  {case['case_id']:<5} r{rep}  {row['action']:<22} "
                              f"{row['latency_ms']:>8.0f} ms (wait {row['retry_wait_ms'] or 0:>6.0f})  "
                              f"llm={row['llm_calls']}  {row['notes']}", flush=True)
                    rows.append(row)
                    writer.writerow(row)
                    f.flush()

    architectures = args.architectures
    if appending:  # summarise everything in the file, not just this invocation
        rows = load_rows(out_csv)
        architectures = [a for a in ("rules_only", "single", "staged") if any(r["architecture"] == a for r in rows)]
    max_rep = max((int(r["repeat"]) for r in rows), default=1)
    summary = summarise(rows, architectures, max(args.repeats, max_rep), len(cases), mode_note)
    (ROOT / "evals" / f"{out_name}.md").write_text(summary, encoding="utf-8")
    print("\n" + summary)
    print(f"Wrote {out_csv.relative_to(ROOT)} and evals/{out_name}.md in {time.perf_counter() - started:.1f}s", flush=True)


if __name__ == "__main__":
    main()
