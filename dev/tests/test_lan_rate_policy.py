"""Threshold, evidence freshness and demand-aware fallback decisions."""
from dataclasses import replace

import pytest

from dreamsync.output.lan_rate_policy import LanDeliverySample, LanRatePolicy


def sample(step, delivered=19, expected=30, source="visual"):
    return LanDeliverySample("id", "ip", (step - 1) * 5, step * 5, expected, delivered, source)


@pytest.mark.parametrize("delivered, switches", [(19, True), (20, False), (21, False), (0, True)])
def test_strict_threshold_and_sustained_failure(delivered, switches):
    policy = LanRatePolicy()
    for step in range(1, 4):
        assert policy.record(sample(step, delivered), now=step * 5)
        assert policy.should_fallback(step * 5) is (switches and step == 3)


def test_brief_dip_and_recovery_reset_streak():
    policy = LanRatePolicy()
    for step, delivered in enumerate([15, 15, 20, 15, 15], 1):
        policy.record(sample(step, delivered), now=step * 5)
        assert not policy.should_fallback(step * 5)


@pytest.mark.parametrize("expected", [0, 5, 15])
def test_idle_or_intentionally_low_demand_does_not_switch(expected):
    policy = LanRatePolicy()
    for step in range(1, 5):
        policy.record(sample(step, delivered=0, expected=expected), now=step * 5)
    assert not policy.should_fallback(20)


def test_duplicate_and_overlapping_samples_do_not_accumulate():
    policy = LanRatePolicy()
    assert policy.record(sample(1), now=5)
    assert not policy.record(sample(1), now=5)
    assert not policy.record(replace(sample(2), started_at=4), now=10)
    assert not policy.should_fallback(10)


def test_missing_window_or_different_source_resets_persistence():
    for last in [sample(4), sample(3, source="device_ack")]:
        policy = LanRatePolicy()
        policy.record(sample(1), now=5)
        policy.record(sample(2), now=10)
        policy.record(last, now=last.ended_at)
        assert not policy.should_fallback(last.ended_at)


def test_stale_future_and_short_observations_never_trigger():
    policy = LanRatePolicy()
    assert not policy.record(sample(1), now=11)
    assert not policy.record(sample(2), now=9)
    for step in range(1, 4):
        policy.record(sample(step), now=step * 5)
    assert policy.should_fallback(15)
    assert not policy.should_fallback(20.01)
    assert not policy.record(replace(sample(4), started_at=19), now=20)
    assert not policy.should_fallback(20)


@pytest.mark.parametrize("changes", [
    {"source": "udp_send"}, {"source": "scan_reply"},
    {"delivered_frames": -1}, {"expected_frames": 1.5},
    {"delivered_frames": 31}, {"started_at": float("nan")},
    {"ended_at": 0},
])
def test_invalid_or_host_only_evidence_rejected(changes):
    with pytest.raises(ValueError):
        LanRatePolicy().record(replace(sample(1), **changes), now=5)
