"""EDGAR access-layer tests: CIK padding, ticker->CIK re-keying, and cache-hit paths that
must not touch the network. Hermetic -- caches are redirected to tmp dirs."""
import json

import edgar


def test_ten_digit_cik_zero_pads():
    assert edgar._ten_digit_cik(320193) == "0000320193"
    assert edgar._ten_digit_cik("1364479") == "0001364479"


def test_pick_unit_prefers_usd_then_shares():
    from extract import _pick_unit
    assert _pick_unit({"shares": [], "USD": []}) == "USD"
    assert _pick_unit({"shares": []}) == "shares"
    assert _pick_unit({}) is None


def test_load_ticker_cik_map_rekeys_by_ticker(tmp_path, monkeypatch):
    cache = tmp_path / "company_tickers.json"
    cache.write_text(json.dumps({
        "0": {"cik_str": 320193, "ticker": "aapl", "title": "Apple"},
        "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft"},
    }))
    monkeypatch.setattr(edgar, "TICKERS_CACHE", cache)
    m = edgar.load_ticker_cik_map()
    assert m["AAPL"] == 320193 and m["MSFT"] == 789019      # upper-cased keys
    assert edgar.resolve_cik("aapl", m) == 320193
    assert edgar.resolve_cik("NOPE", m) is None


def _forbid_network(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("network call attempted on a cache hit")
    monkeypatch.setattr(edgar, "_rate_limited_get", boom)


def test_get_companyfacts_cache_hit_no_network(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "COMPANYFACTS_DIR", tmp_path)
    (tmp_path / "CIK0000000001.json").write_text(json.dumps({"entityName": "Cached"}))
    _forbid_network(monkeypatch)
    assert edgar.get_companyfacts(1) == {"entityName": "Cached"}


def test_get_submissions_cache_hit_no_network(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "SUBMISSIONS_DIR", tmp_path)
    (tmp_path / "CIK0000000001.json").write_text(json.dumps({"filings": {"recent": {}}}))
    _forbid_network(monkeypatch)
    assert edgar.get_submissions(1) == {"filings": {"recent": {}}}
