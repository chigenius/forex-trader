from __future__ import annotations

import polars as pl

from conftest import make_bars

from forexml.data.validation import validate_bars


def test_duplicate_timestamps_are_quarantined_and_reported():
    bars = make_bars(50)
    with_dup = pl.concat([bars, bars.slice(10, 1)])
    clean, report = validate_bars(with_dup, "EURUSD", "M15")
    assert clean.height == 50
    # both copies of the duplicated timestamp are flagged, not just the "extra" one
    assert len(report.issues_of("duplicate_timestamp")) == 2


def test_nonpositive_spread_is_quarantined():
    bars = make_bars(50)
    bad = bars.clone()
    bad[5, "bid_close"] = 1.5
    bad[5, "ask_close"] = 1.4  # ask < bid
    clean, report = validate_bars(bad, "EURUSD", "M15")
    assert clean.height == 49
    assert len(report.issues_of("nonpositive_spread")) == 1


def test_price_spike_is_quarantined():
    bars = make_bars(100)
    bad = bars.clone()
    bad[50, "close"] = bad[50, "close"] * 100  # absurd spike
    clean, report = validate_bars(bad, "EURUSD", "M15")
    assert len(report.issues_of("price_spike")) >= 1
    assert clean.height < bad.height


def test_gap_is_reported_but_not_removed():
    bars = make_bars(50)
    shifted = bars.with_columns(
        pl.when(pl.col("timestamp_utc") >= bars["timestamp_utc"][25])
        .then(pl.col("timestamp_utc") + pl.duration(hours=5))
        .otherwise(pl.col("timestamp_utc"))
        .alias("timestamp_utc")
    )
    clean, report = validate_bars(shifted, "EURUSD", "M15")
    assert len(report.issues_of("gap")) >= 1
    assert clean.height == shifted.height  # gaps are reported, not quarantined


def test_clean_data_produces_no_issues():
    bars = make_bars(200)
    clean, report = validate_bars(bars, "EURUSD", "M15")
    assert report.is_clean
    assert clean.height == bars.height
