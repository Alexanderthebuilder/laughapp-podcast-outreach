"""A check that cannot fail is not a check.

The first live run printed "ZERO MISSES" while having scraped nothing at all:
an empty ground-truth set has no misses. The verdict must distinguish "nothing
was verified" from "everything verified clean".
"""
import pytest

from src.phase1_enumerate import MIN_GROUND_TRUTH, crosscheck_verdict


def test_empty_ground_truth_is_inconclusive_not_a_pass():
    verdict, ok = crosscheck_verdict(0, [])
    assert not ok
    assert "INCONCLUSIVE" in verdict
    assert "ZERO MISSES" not in verdict


@pytest.mark.parametrize("found", [1, 5, MIN_GROUND_TRUTH - 1])
def test_a_handful_of_listings_is_still_inconclusive(found):
    verdict, ok = crosscheck_verdict(found, [])
    assert not ok and "INCONCLUSIVE" in verdict


def test_a_real_clean_result_passes():
    verdict, ok = crosscheck_verdict(240, [])
    assert ok
    assert "ZERO MISSES out of 240" in verdict


def test_misses_fail_and_are_counted():
    verdict, ok = crosscheck_verdict(240, [101, 202, 303])
    assert not ok
    assert "3 MISSES out of 240" in verdict


def test_misses_on_a_tiny_sample_report_inconclusive_first():
    """With too little ground truth we cannot tell a miss from a render
    failure, so the more fundamental problem is reported."""
    verdict, ok = crosscheck_verdict(3, [101])
    assert not ok and "INCONCLUSIVE" in verdict
