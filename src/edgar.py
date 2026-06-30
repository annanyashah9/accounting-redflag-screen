"""
SEC EDGAR data-fetch layer.

Responsibilities (and ONLY these -- keep tag-mapping/scoring out of here):
  * resolve ticker -> CIK via SEC's company_tickers.json
  * fetch each company's companyfacts JSON
  * cache every response to data/ so scores are reproducible against a fixed snapshot
  * be a polite client: declared User-Agent + request rate limiting

We deliberately use the structured companyfacts XBRL API and never parse 10-K HTML.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from config import (
    COMPANY_TICKERS_URL,
    COMPANYFACTS_URL,
    SEC_MAX_REQUESTS_PER_SECOND,
    USER_AGENT,
)

# data/ lives at the repo root (one level up from this src/ file).
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
COMPANYFACTS_DIR = DATA_DIR / "companyfacts"
TICKERS_CACHE = DATA_DIR / "company_tickers.json"

_MIN_INTERVAL = 1.0 / SEC_MAX_REQUESTS_PER_SECOND
_last_request_time = 0.0


def _headers() -> dict:
    return {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}


def _rate_limited_get(url: str) -> requests.Response:
    """GET that spaces requests to stay under the SEC's fair-access ceiling."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    resp = requests.get(url, headers=_headers(), timeout=30)
    _last_request_time = time.monotonic()
    resp.raise_for_status()
    return resp


def _ten_digit_cik(cik: int | str) -> str:
    """SEC companyfacts URLs use a zero-padded 10-digit CIK."""
    return str(int(cik)).zfill(10)


def load_ticker_cik_map(force_refresh: bool = False) -> dict[str, int]:
    """Return {TICKER -> CIK int}, cached on disk.

    company_tickers.json is keyed by an arbitrary index; we re-key by uppercase ticker.
    """
    if TICKERS_CACHE.exists() and not force_refresh:
        raw = json.loads(TICKERS_CACHE.read_text())
    else:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        raw = _rate_limited_get(COMPANY_TICKERS_URL).json()
        TICKERS_CACHE.write_text(json.dumps(raw))

    mapping: dict[str, int] = {}
    for entry in raw.values():
        mapping[entry["ticker"].upper()] = int(entry["cik_str"])
    return mapping


def resolve_cik(ticker: str, ticker_map: dict[str, int]) -> int | None:
    """Map a ticker to its CIK, or None if SEC doesn't list it (e.g. delisted name)."""
    return ticker_map.get(ticker.upper())


def get_companyfacts(cik: int | str, force_refresh: bool = False) -> dict | None:
    """Fetch (and cache) the companyfacts JSON for one CIK.

    Returns the parsed dict, or None if SEC has no companyfacts for that CIK (404) --
    common for delisted / foreign filers. Callers should treat None as "no data,
    flag it" rather than an error.
    """
    cik10 = _ten_digit_cik(cik)
    cache_path = COMPANYFACTS_DIR / f"CIK{cik10}.json"

    if cache_path.exists() and not force_refresh:
        return json.loads(cache_path.read_text())

    COMPANYFACTS_DIR.mkdir(parents=True, exist_ok=True)
    url = COMPANYFACTS_URL.format(cik10=cik10)
    try:
        resp = _rate_limited_get(url)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise

    data = resp.json()
    cache_path.write_text(json.dumps(data))
    return data
