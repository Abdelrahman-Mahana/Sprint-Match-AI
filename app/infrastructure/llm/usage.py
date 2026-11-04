"""Per-run LLM usage accounting (calls and tokens), independent of the provider.

A run opens ``metered()``; every completed provider call inside it adds to the meter. The meter is shared by
reference through ``contextvars``, so LangGraph nodes executed in worker threads (copied contexts) still add
to the same run's meter.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class UsageMeter:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, prompt_tokens: int | None, completion_tokens: int | None) -> None:
        with self._lock:
            self.calls += 1
            self.prompt_tokens += prompt_tokens or 0
            self.completion_tokens += completion_tokens or 0

    def snapshot(self) -> tuple[int, int, int]:
        with self._lock:
            return self.calls, self.prompt_tokens, self.completion_tokens


_current: ContextVar[UsageMeter | None] = ContextVar("sprintmatch_llm_usage", default=None)


@contextmanager
def metered(meter: UsageMeter | None = None) -> Iterator[UsageMeter]:
    meter = meter or UsageMeter()
    token = _current.set(meter)
    try:
        yield meter
    finally:
        _current.reset(token)


def current_meter() -> UsageMeter | None:
    return _current.get()


def record_usage(prompt_tokens: int | None, completion_tokens: int | None) -> None:
    meter = _current.get()
    if meter is not None:
        meter.add(prompt_tokens, completion_tokens)
