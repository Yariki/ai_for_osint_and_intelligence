import pytest

from ndm.core.estimate import Estimate, estimate, estimate_profile, probe_count
from ndm.core.ports import PortProfile

# --- Check 1B.2 (NFR-PRF-1) ---


@pytest.mark.parametrize(
    ("profile", "probes"),
    [
        (PortProfile.TOP_100, 25_600),
        (PortProfile.TOP_1000, 256_000),
        (PortProfile.FULL, 16_776_960),
    ],
)
def test_256_hosts_give_the_probe_counts_of_the_specification(
    profile: PortProfile, probes: int
) -> None:
    assert estimate_profile(256, profile, rate_pps=1000, retries=0).probes == probes


@pytest.mark.parametrize(
    ("profile", "rate", "seconds"),
    [
        # NFR-PRF-1: about 26 seconds, 4.3 minutes and 4.7 hours at 1,000 packets per second,
        (PortProfile.TOP_100, 1000, 25.6),
        (PortProfile.TOP_1000, 1000, 256.0),
        (PortProfile.FULL, 1000, 16_776.96),
        # and about 5 seconds, 51 seconds and 56 minutes at 5,000.
        (PortProfile.TOP_100, 5000, 5.12),
        (PortProfile.TOP_1000, 5000, 51.2),
        (PortProfile.FULL, 5000, 3_355.392),
    ],
)
def test_durations_of_the_specification(profile: PortProfile, rate: int, seconds: float) -> None:
    assert estimate_profile(256, profile, rate_pps=rate, retries=0).seconds == pytest.approx(
        seconds
    )


# --- Retries ---


def test_retries_raise_the_upper_bound_only() -> None:
    result = estimate(hosts=64, ports=100, rate_pps=1000, retries=2)

    assert result == Estimate(probes=6_400, max_probes=19_200, seconds=6.4, max_seconds=19.2)


def test_without_retries_the_bounds_are_equal() -> None:
    result = estimate(hosts=1, ports=1000, rate_pps=500, retries=0)

    assert (result.probes, result.seconds) == (result.max_probes, result.max_seconds) == (1000, 2.0)


# --- Edge cases ---


def test_nothing_to_scan() -> None:
    assert estimate(hosts=0, ports=1000, rate_pps=1000, retries=1).probes == 0
    assert probe_count(256, 0) == 0


@pytest.mark.parametrize(
    "arguments",
    [
        {"hosts": -1, "ports": 10, "rate_pps": 1000, "retries": 0},
        {"hosts": 1, "ports": -10, "rate_pps": 1000, "retries": 0},
        {"hosts": 1, "ports": 10, "rate_pps": 0, "retries": 0},
        {"hosts": 1, "ports": 10, "rate_pps": 1000, "retries": -1},
    ],
)
def test_invalid_input_is_an_error(arguments: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        estimate(**arguments)
