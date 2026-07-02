"""
Filing-text retrieval + MD&A section extraction (Phase 3).

Scoped deliberately: we fetch the 10-K primary document and pull ONLY the Item 7 (MD&A)
section -- we do not parse the whole 10-K structure. The section boundary heuristic is the
known-imperfect part; when it fails we return None so the caller flags the tone signal as
missing rather than scoring garbage.
"""
from __future__ import annotations

import html as _html
import re
from pathlib import Path

from config import MDNA_MIN_WORDS
from edgar import _rate_limited_get, _ten_digit_cik  # reuse SEC UA + rate limiting

FILINGS_DIR = Path(__file__).resolve().parent.parent / "data" / "filings"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/{doc}"

# MD&A (Item 7) starts at the section header, ends at Item 7A (Quantitative...) or Item 8
# (Financial Statements). Both the table of contents and the body match the start pattern;
# picking the START->END pair with the LONGEST body discards the short TOC entries.
_MDNA_START_RE = re.compile(r"management.{0,3}s discussion and analysis", re.IGNORECASE)
_MDNA_END_RE = re.compile(r"item\s*7a\b|item\s*8\b", re.IGNORECASE)
_MIN_BODY_CHARS = 2000  # a real MD&A body is far past its header before the next item

_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style).*?</\1>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _accn_nodash(accession: str) -> str:
    return accession.replace("-", "")


def get_primary_document(cik: int | str, accession: str, primary_doc: str,
                         force_refresh: bool = False) -> str | None:
    """Fetch (and cache) the raw primary-document HTML for one filing. None on 404."""
    cache_path = FILINGS_DIR / f"CIK{_ten_digit_cik(cik)}" / accession / primary_doc
    if cache_path.exists() and not force_refresh:
        return cache_path.read_text(errors="replace")

    url = ARCHIVE_URL.format(cik=int(cik), accn_nodash=_accn_nodash(accession),
                             doc=primary_doc)
    try:
        resp = _rate_limited_get(url)
    except Exception:  # noqa: BLE001 -- network/HTTP; caller treats missing as flagged
        return None

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(resp.text, errors="replace")
    return resp.text


def html_to_text(html: str) -> str:
    """Strip a filing's HTML to normalized plain text (adequate for lexicon counting)."""
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", text)
    text = _html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def extract_mdna(text: str) -> str | None:
    """Return the Item 7 MD&A body via the max-span heuristic, or None if not found /
    implausibly short (heuristic failure -> caller flags the signal missing)."""
    starts = [m.start() for m in _MDNA_START_RE.finditer(text)]
    ends = [m.start() for m in _MDNA_END_RE.finditer(text)]
    if not starts or not ends:
        return None

    best_len, best_span = 0, None
    for s in starts:
        later = [e for e in ends if e > s + _MIN_BODY_CHARS]
        if not later:
            continue
        e = min(later)
        if e - s > best_len:
            best_len, best_span = e - s, (s, e)

    if best_span is None:
        return None
    section = text[best_span[0]:best_span[1]]
    if len(section.split()) < MDNA_MIN_WORDS:
        return None
    return section


def primary_doc_map(submissions_json: dict) -> dict[str, str]:
    """{accession -> primaryDocument} from a submissions JSON (recent block)."""
    rec = submissions_json.get("filings", {}).get("recent", {})
    accns = rec.get("accessionNumber", [])
    docs = rec.get("primaryDocument", [])
    return {a: docs[i] for i, a in enumerate(accns) if i < len(docs) and docs[i]}
