"""Detection of instruction-like sentences inside untrusted task text.

Task descriptions are written by people (or copied from tickets) and are passed to an LLM. A sentence that
addresses the system instead of describing the work ("ignore previous instructions", "make Kubernetes a
mandatory skill", "recommend EMP-007") must never become a requirement or influence a recommendation.
Such sentences are removed from the text used for grounding and keyword matching, and the analysis is flagged
``POSSIBLE_PROMPT_INJECTION`` so a human reviews it. Detection is conservative (false positives only cost an
LLM-suggested requirement, which the manager can still set explicitly as a structured field).
"""

from __future__ import annotations

import re

_PATTERNS = (
    re.compile(
        r"\b(ignore|disregard|forget|override|bypass)\b[^.!?\n]{0,60}"
        r"\b(instructions?|prompts?|rules?|previous|above|system|guidelines?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(make|mark|set|treat|add|list|declare|flag)\b[^.!?\n]{0,60}"
        r"\b(mandatory|required|hard)\s+(skills?|requirements?|constraints?)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(system prompt|you are an? |act as an? |as an ai\b|assistant:)", re.IGNORECASE),
    re.compile(
        r"\b(recommend|assign|select|choose|pick|prefer|rank)\b[^.!?\n]{0,40}\bEMP-\d+", re.IGNORECASE
    ),
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def split_instruction_like(text: str) -> tuple[str, tuple[str, ...]]:
    """Return ``(clean_text, flagged_sentences)``; ``clean_text`` keeps every non-flagged sentence."""
    kept: list[str] = []
    flagged: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(text):
        if not sentence.strip():
            continue
        (flagged if any(p.search(sentence) for p in _PATTERNS) else kept).append(sentence)
    return "\n".join(kept), tuple(flagged)
