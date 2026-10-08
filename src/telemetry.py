from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RunTelemetryCounter:
    """Per-request counters, converted to RunTelemetry on the decision."""
    llm_calls: int = 0
    tool_calls: int = 0
    tool_names: list[str] = field(default_factory=list)
    failed_tools: list[str] = field(default_factory=list)
    llm_latency_ms: float = 0.0
    retry_wait_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    replayed_ms: float = 0.0  # recorded LLM time+wait added back when replaying

    def record_llm_call(self, latency_ms: float = 0.0, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        self.llm_calls += 1
        self.llm_latency_ms += latency_ms
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens

    def record_tool_call(self, name: str, ok: bool = True) -> None:
        self.tool_calls += 1
        self.tool_names.append(name)
        if not ok:
            self.failed_tools.append(name)

    def record_retry_wait(self, ms: float) -> None:
        self.retry_wait_ms += ms
