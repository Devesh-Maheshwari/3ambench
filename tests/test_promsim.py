"""Reference evaluator: exact Prometheus semantics (no promtool needed)."""

from fractions import Fraction as F

from alertforge import promsim as ps


def test_encode_decode_roundtrip():
    vals = [100, 110, 120, 130, 130, 130, 5, None, None, ps.STALE, 7]
    assert ps.decode_values(ps.encode_values(vals)) == vals


def test_encode_uses_expanding_notation():
    assert ps.encode_values([0, 5, 10, 15]) == "0+5x3"
    assert ps.encode_values([9, 9, 9]) == "9+0x2"
    assert ps.encode_values([3, 1]) == "3-2x1"


def test_rate_full_window_extrapolates_to_window():
    # 5m window, samples every minute, +60/min: rate = 1/s exactly after extrapolation.
    vals = [10_000 + 60 * m for m in range(20)]
    assert ps.extrapolated(vals, 10, 5) == F(1)


def test_rate_left_open_window_and_too_few_samples():
    vals = [0, 60]
    assert ps.extrapolated(vals, 1, 1) is None  # (0, 1] holds a single sample


def test_rate_counter_reset_is_handled():
    vals = [10_000, 10_060, 10_120, 30, 90]  # reset at minute 3
    r = ps.extrapolated(vals, 4, 5, is_rate=False)
    assert r is not None and r > 0


def test_histogram_quantile_interpolates_inside_bucket():
    buckets = [(F("0.1"), F(50)), (F("0.5"), F(90)), (F(1), F(100)), (None, F(100))]
    # rank 0.9*100 = 90 lands exactly at the top of the 0.5 bucket
    assert ps.histogram_quantile(F(9, 10), buckets) == F("0.5")
    # rank 95 → halfway through (0.5, 1]
    assert ps.histogram_quantile(F(95, 100), buckets) == F("0.75")


def test_histogram_quantile_inf_bucket_returns_second_highest_bound():
    buckets = [(F("0.5"), F(10)), (None, F(100))]
    assert ps.histogram_quantile(F(99, 100), buckets) == F("0.5")


def test_instant_lookback_and_stale():
    vals = [1, 1, ps.STALE, None, None]
    assert ps.instant(vals, 1) == 1
    assert ps.instant(vals, 3) is None
    assert ps.instant([1] + [None] * 10, 7) is None  # older than 5m


def test_firing_timeline_for_semantics():
    cond = {5, 6, 7, 8, 9}
    fires = ps.firing_timeline(10, lambda t: {"a"} if t in cond else set(), 2)
    assert [("a" in f) for f in fires] == [False] * 7 + [True, True, True, False]


def test_near_threshold_detector():
    assert ps.near(F("0.0144000001"), F("0.0144"))
    assert not ps.near(F("0.015"), F("0.0144"))
