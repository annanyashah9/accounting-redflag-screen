"""
OPTIONAL LLM nuance pass (Phase 3) -- supplementary, OFF by default.

The lexicon signals in tone.py are the transparent, reproducible core of the tone screen.
This module adds an optional single-call-per-document nuanced read for cases where word
counts miss context (e.g. defensiveness, tone shifts a lexicon can't see). It is:
  * disabled unless ANTHROPIC_API_KEY is set AND run_phase3 is invoked with --llm,
  * never the primary signal -- it augments, it does not replace,
  * dependency-light: the `anthropic` SDK is imported lazily so the rest of Phase 3 runs
    without it.

Kept separate on purpose so the defensible core stands entirely on its own.
"""
from __future__ import annotations

import json
import os

# A small, fast model is appropriate for a supplementary nuance pass.
DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"
_MAX_CHARS = 12000  # cap document length sent to the model

_PROMPT = (
    "You are analyzing the Management's Discussion & Analysis (MD&A) section of a 10-K. "
    "Return ONLY a JSON object with these keys:\n"
    '  "overall_tone": number in [-1,1] (negative..positive),\n'
    '  "evasiveness": number in [0,1] (how hedged/evasive the language is),\n'
    '  "confidence_in_outlook": number in [0,1],\n'
    '  "notable_shift": one short sentence on any tonal red flag, or "none".\n'
    "Judge the FINANCIAL communication tone, not general sentiment. Text:\n\n"
)


def llm_available() -> bool:
    """True only if an API key is configured (the SDK import is checked lazily)."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def analyze_tone_llm(text: str, model: str = DEFAULT_LLM_MODEL) -> dict | None:
    """One nuanced tone read for a document, or None if the pass is unavailable/fails.

    Returns a dict with keys overall_tone / evasiveness / confidence_in_outlook /
    notable_shift (all supplementary to the lexicon signals)."""
    if not llm_available():
        return None
    try:
        import anthropic  # lazy: not a hard dependency of Phase 3
    except ImportError:
        return None

    try:
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=model,
            max_tokens=300,
            messages=[{"role": "user", "content": _PROMPT + text[:_MAX_CHARS]}],
        )
        raw = "".join(block.text for block in msg.content if block.type == "text")
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            return None
        return json.loads(raw[start:end + 1])
    except Exception:  # noqa: BLE001 -- supplementary; never break the pipeline
        return None
