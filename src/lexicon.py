"""
Loughran-McDonald finance lexicon loader + tokenizer (Phase 3).

We use LM (built from 10-Ks) rather than a general-purpose sentiment model because general
models misclassify neutral financial vocabulary -- "liability", "cost", "risk", "capital",
"tax" -- as negative. LM assigns finance-aware categories (Negative, Positive, Uncertainty,
Litigious, Strong/Weak Modal), which is the whole point of the tone signal's credibility.

The dictionary CSV is fetched once and cached to data/lexicons/. Category columns hold the
YEAR a word entered that category (0 = not in the category), so membership is `cell != 0`.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import requests

from config import LM_CATEGORIES, LM_DICT_URL, USER_AGENT

LEXICON_DIR = Path(__file__).resolve().parent.parent / "data" / "lexicons"
LM_CACHE = LEXICON_DIR / "LoughranMcDonald_MasterDictionary.csv"

_TOKEN_RE = re.compile(r"[A-Za-z']+")
_categories_cache: dict[str, frozenset[str]] | None = None


def get_lm_dictionary(force_refresh: bool = False) -> Path:
    """Fetch (once) and cache the LM Master Dictionary CSV; return its path."""
    if LM_CACHE.exists() and not force_refresh:
        return LM_CACHE
    LEXICON_DIR.mkdir(parents=True, exist_ok=True)
    resp = requests.get(LM_DICT_URL, headers={"User-Agent": USER_AGENT}, timeout=90)
    resp.raise_for_status()
    LM_CACHE.write_text(resp.text)
    return LM_CACHE


def load_lm_categories(force_refresh: bool = False) -> dict[str, frozenset[str]]:
    """Return {category -> set of UPPERCASE words}, memoized for the process."""
    global _categories_cache
    if _categories_cache is not None and not force_refresh:
        return _categories_cache

    path = get_lm_dictionary(force_refresh=force_refresh)
    sets: dict[str, set[str]] = {cat: set() for cat in LM_CATEGORIES}
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            word = row["Word"].strip().upper()
            for cat in LM_CATEGORIES:
                val = (row.get(cat) or "0").strip()
                if val not in ("0", ""):
                    sets[cat].add(word)
    _categories_cache = {cat: frozenset(words) for cat, words in sets.items()}
    return _categories_cache


def tokenize(text: str) -> list[str]:
    """Split text into UPPERCASE alphabetic tokens (matching the LM dictionary casing)."""
    return [t.upper() for t in _TOKEN_RE.findall(text)]
