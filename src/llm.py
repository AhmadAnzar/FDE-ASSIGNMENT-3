"""Thin LLM transport: one JSON-mode chat call validated against a Pydantic schema.

- Client is created lazily, so importing the package never needs an API key.
- Rate limits are retried with backoff; the wait is recorded separately so
  evaluation can report latency with and without provider throttling.
- Output is validated against the schema; one repair attempt is made on
  invalid JSON / schema errors. Anything else raises LLMError, which the
  pipeline turns into a deterministic fallback decision.
- Record / replay (env LLM_CACHE=record|replay): record stores every model
  response with its measured latency and tokens; replay feeds them back so the
  whole evaluation re-runs in seconds without an API key. A prompt that changed
  since recording is a cache miss and fails loudly (ReplayMiss).
"""
from __future__ import annotations

import json
import os
import re
import hashlib
import time
from collections import defaultdict
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from src.telemetry import RunTelemetryCounter

DEFAULT_MODEL = "qwen/qwen3.8-27b"
MAX_RATE_LIMIT_RETRIES = 4

T = TypeVar("T", bound=BaseModel)

_client = None


class LLMError(RuntimeError):
    pass


class ReplayMiss(RuntimeError):
    """Replay requested a response that was never recorded (prompt or model changed)."""


DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[1] / "evals" / "llm_cache.jsonl"
_replay_entries: dict[str, list[dict]] | None = None
_occurrences: dict[str, int] = defaultdict(int)


def max_output_tokens() -> int:
    """Cap per response; must stay under the provider's output-tokens-per-minute limit (1,000 on Groq free tier)."""
    return int(os.getenv("LLM_MAX_TOKENS") or 900)


def max_wait_seconds() -> float:
    """Longest total rate-limit wait per call before falling back to rules-only.

    Library default suits batch callers (graders, eval harnesses); the UI sets LLM_MAX_WAIT_S=20.
    """
    return float(os.getenv("LLM_MAX_WAIT_S") or 120)


def cache_mode() -> str:
    return (os.getenv("LLM_CACHE") or "off").lower()


def cache_path() -> Path:
    return Path(os.getenv("LLM_CACHE_PATH") or DEFAULT_CACHE_PATH)


def _cache_key(messages: list[dict]) -> str:
    payload = json.dumps({"model": model_name(), "messages": messages}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_replay() -> dict[str, list[dict]]:
    global _replay_entries
    if _replay_entries is None:
        _replay_entries = defaultdict(list)
        if not cache_path().exists():
            raise ReplayMiss(f"no recorded responses at {cache_path()} - run the evaluation with --record first")
        for line in cache_path().read_text(encoding="utf-8").splitlines():
            if line.strip():
                entry = json.loads(line)
                _replay_entries[entry["key"]].append(entry)
    return _replay_entries


def model_name() -> str:
    return os.getenv("MODEL_NAME") or DEFAULT_MODEL


def _get_client():
    global _client
    if _client is None:
        from groq import Groq

        key = os.getenv("GROQ_API_KEY")
        if not key:
            raise LLMError("GROQ_API_KEY is not set")
        _client = Groq(api_key=key, max_retries=0)
    return _client


def _retry_after_seconds(exc: Exception, attempt: int) -> float:
    text = str(exc)
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", text)
    if m:
        return min(60.0, float(m.group(1) or 0) * 60 + float(m.group(2)) + 0.5)
    return min(30.0, 2.0 * (2 ** attempt))


def _strip(content: str) -> str:
    content = content or ""
    if "</think>" in content:
        content = content.split("</think>")[-1]
    content = content.strip()
    if content.startswith("```"):
        content = "\n".join(l for l in content.splitlines() if not l.strip().startswith("```"))
    return content.strip()


def _create(messages: list[dict], telemetry: RunTelemetryCounter) -> str:
    """One model call -> raw content. Handles rate limits and record/replay."""
    key = _cache_key(messages)
    occurrence = _occurrences[key]
    _occurrences[key] += 1

    if cache_mode() == "replay":
        entries = _load_replay().get(key)
        if not entries:
            raise ReplayMiss("no recorded response for this prompt (prompt, policy or model changed since recording)")
        e = entries[occurrence % len(entries)]
        telemetry.record_retry_wait(e["wait_ms"])
        telemetry.record_llm_call(latency_ms=e["latency_ms"], prompt_tokens=e["prompt_tokens"], completion_tokens=e["completion_tokens"])
        telemetry.replayed_ms += e["latency_ms"] + e["wait_ms"]
        return e["content"]

    client = _get_client()
    waited_ms = 0.0
    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        start = time.perf_counter()
        try:
            resp = client.chat.completions.create(
                model=model_name(),
                messages=messages,
                temperature=0,
                max_tokens=max_output_tokens(),
                response_format={"type": "json_object"},
                reasoning_format="hidden",
            )
        except Exception as exc:
            text = str(exc).lower()
            is_rate_limit = "rate_limit" in text or getattr(exc, "status_code", None) == 429
            if "request too large" in text:
                # Can never fit the provider's per-request/per-minute budget: retrying only wastes time.
                raise LLMError("request exceeds the provider's per-minute token limit") from exc
            if is_rate_limit and ("per day" in text or "(tpd)" in text or "(rpd)" in text):
                # Daily quota: retrying for minutes helps nobody - fail fast to the rules-only decision.
                raise LLMError("daily AI quota exhausted on the provider (tokens/requests per day)") from exc
            if is_rate_limit and attempt < MAX_RATE_LIMIT_RETRIES:
                wait = _retry_after_seconds(exc, attempt)
                if (waited_ms / 1000) + wait > max_wait_seconds():
                    raise LLMError(f"provider rate limit: would need to wait {wait:.0f}s more") from exc
                telemetry.record_retry_wait(wait * 1000)
                waited_ms += wait * 1000
                time.sleep(wait)
                continue
            raise LLMError(f"{type(exc).__name__}: {str(exc)[:300]}") from exc
        latency_ms = (time.perf_counter() - start) * 1000
        usage = getattr(resp, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        telemetry.record_llm_call(latency_ms=latency_ms, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        content = resp.choices[0].message.content or ""
        if cache_mode() == "record":
            cache_path().parent.mkdir(parents=True, exist_ok=True)
            with cache_path().open("a", encoding="utf-8") as f:
                f.write(json.dumps({"key": key, "model": model_name(), "latency_ms": round(latency_ms, 1),
                                    "wait_ms": round(waited_ms, 1), "prompt_tokens": prompt_tokens,
                                    "completion_tokens": completion_tokens, "content": content}, ensure_ascii=False) + "\n")
        return content
    raise LLMError("rate limit retries exhausted")


def chat_json(system: str, user: str, schema: type[T], telemetry: RunTelemetryCounter) -> T:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last_error = ""
    for _ in range(2):  # first attempt + one repair attempt
        content = _strip(_create(messages, telemetry))
        try:
            return schema.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)[:800]
            messages = messages + [
                {"role": "assistant", "content": content[:4000]},
                {"role": "user", "content": f"Your output did not match the required JSON schema:\n{last_error}\nReturn ONLY the corrected JSON object."},
            ]
    raise LLMError(f"invalid structured output after repair: {last_error[:300]}")
