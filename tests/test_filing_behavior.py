"""Late-filing red-flag tests (Phase 4 strengthening). Hermetic -- synthetic submissions."""
from filing_behavior import late_filing_flags


def _subs(rows):
    """rows: list of (form, filingDate, reportDate) -> submissions-shaped dict."""
    return {"filings": {"recent": {
        "form": [r[0] for r in rows],
        "filingDate": [r[1] for r in rows],
        "reportDate": [r[2] for r in rows],
    }}}


def test_late_10k_gap_flags():
    df = late_filing_flags(_subs([("10-K", "2021-04-30", "2020-12-31")]), 1)  # 120d
    row = df.set_index("fiscal_period_end").loc["2020-12-31"]
    assert row["late_filing"] and "120d" in row["late_reason"]


def test_on_time_10k_not_flagged():
    df = late_filing_flags(_subs([("10-K", "2021-02-14", "2020-12-31")]), 1)  # 45d
    assert not df.iloc[0]["late_filing"]


def test_nt_10k_flags_period_even_if_10k_on_time():
    df = late_filing_flags(_subs([
        ("10-K", "2021-02-14", "2020-12-31"),   # on-time
        ("NT 10-K", "2021-03-01", ""),          # late-notice within window
    ]), 1)
    row = df.set_index("fiscal_period_end").loc["2020-12-31"]
    assert row["late_filing"] and "NT 10-K" in row["late_reason"]


def test_gap_judged_on_original_10k_not_amendment():
    # A late 10-K/A must not make an on-time original filing look late.
    df = late_filing_flags(_subs([
        ("10-K", "2021-02-14", "2020-12-31"),
        ("10-K/A", "2022-06-01", "2020-12-31"),
    ]), 1)
    assert not df.set_index("fiscal_period_end").loc["2020-12-31", "late_filing"]


def test_period_key_is_exact_period_end_date():
    df = late_filing_flags(_subs([("10-K", "2016-04-30", "2016-01-03")]), 1)  # Jan FY-end
    assert df.iloc[0]["fiscal_period_end"] == "2016-01-03"   # joins on the exact date
    assert df.iloc[0]["late_filing"]
