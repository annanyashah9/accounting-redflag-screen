"""
Filing-behavior red flag (Phase 4 strengthening).

Late filing is an independent, leading governance signal: a firm that files its 10-K past the
SEC's statutory deadline -- or files an NT 10-K "we can't file on time" notice -- is waving a
flag long known to precede accounting trouble. It is orthogonal to the financial ratios and
the tone signals, so it makes the >=2-corroboration rule *more* meaningful, not less. The
threshold is the SEC's own deadline (config.LATE_FILING_DAYS), not a number fit to the cases.

Everything comes from the cached EDGAR submissions JSON -- no new fetching, no re-computation.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from config import LATE_FILING_DAYS

# An NT 10-K counts against a fiscal period if filed within this window after period-end.
_NT_WINDOW_DAYS = 180


def _parse(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except (ValueError, TypeError):
        return None


def late_filing_flags(submissions_json: dict, cik: int) -> pd.DataFrame:
    """Per fiscal period (keyed on the exact period-end date, so it lines up with the screen's
    `fiscal_period_end` for any fiscal calendar): was the 10-K late, or was an NT 10-K filed?"""
    rec = submissions_json.get("filings", {}).get("recent", {})
    forms = rec.get("form", [])
    filed = rec.get("filingDate", [])
    reported = rec.get("reportDate", [""] * len(forms))

    by_period: dict[str, list] = {}   # period_end -> [(form, filed_date, period_end_date)]
    nt_dates: list[date] = []
    for i, form in enumerate(forms):
        fd = _parse(filed[i]) if i < len(filed) else None
        rp = reported[i] if i < len(reported) else ""
        if form in ("10-K", "10-K/A"):
            pe = _parse(rp)
            if pe and fd:
                by_period.setdefault(rp, []).append((form, fd, pe))
        elif form.startswith("NT 10-K") and fd:
            nt_dates.append(fd)

    rows = []
    for period_end, recs in by_period.items():
        pe = recs[0][2]
        # Judge the ORIGINAL 10-K (prefer form "10-K" over "/A", then earliest filed).
        form, orig_filed, _ = sorted(recs, key=lambda r: (r[0] != "10-K", r[1]))[0]
        gap = (orig_filed - pe).days
        late_gap = gap > LATE_FILING_DAYS
        nt_hit = any(pe <= d <= pe + timedelta(days=_NT_WINDOW_DAYS) for d in nt_dates)

        reasons = []
        if late_gap:
            reasons.append(f"10-K filed {gap}d after period-end (> {LATE_FILING_DAYS})")
        if nt_hit:
            reasons.append("NT 10-K late-notice filed")

        rows.append({
            "cik": cik, "fiscal_period_end": period_end, "fiscal_year": pe.year,
            "late_filing": bool(late_gap or nt_hit), "late_reason": "; ".join(reasons),
        })

    return pd.DataFrame(rows, columns=["cik", "fiscal_period_end", "fiscal_year",
                                       "late_filing", "late_reason"])
