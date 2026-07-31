"""The on-disk scrape cache."""

import pytest

from ffanalytics import cache


@pytest.fixture(autouse=True)
def temporary_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("FFANALYTICS_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("FFANALYTICS_NO_CACHE", raising=False)
    yield tmp_path


def test_a_saved_value_comes_back():
    cache.save("scrape_cbs", {"QB": [1, 2, 3]})
    assert cache.load("scrape_cbs") == {"QB": [1, 2, 3]}


def test_a_missing_key_is_not_an_error():
    assert cache.load("never_saved") is None


def test_an_expired_entry_is_dropped_rather_than_served():
    cache.save("scrape_cbs", "stale")
    assert cache.load("scrape_cbs", ttl=0) is None
    assert cache.load("scrape_cbs") is None  # and it is gone for good


def test_clearing_removes_everything():
    cache.save("a", 1)
    cache.save("b", 2)
    assert cache.clear() == 2
    assert cache.load("a") is None


def test_clearing_one_key_leaves_the_others():
    cache.save("a", 1)
    cache.save("b", 2)
    assert cache.clear("a") == 1
    assert cache.load("b") == 2


def test_listing_reports_what_is_cached():
    cache.save("scrape_cbs", "x")
    listing = cache.listing()
    assert listing["key"].tolist() == ["scrape_cbs"]
    assert listing["age_minutes"].iloc[0] < 1


def test_caching_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("FFANALYTICS_NO_CACHE", "1")
    cache.save("scrape_cbs", "x")
    assert cache.load("scrape_cbs") is None
