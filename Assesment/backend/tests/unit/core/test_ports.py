from importlib import resources

import pytest

from ndm.core.ports import PortProfile, profile_ports, service_name


def test_lists_hold_exactly_100_and_1000_distinct_ports() -> None:
    top_100 = profile_ports(PortProfile.TOP_100)
    top_1000 = profile_ports(PortProfile.TOP_1000)

    assert len(top_100) == len(set(top_100)) == 100
    assert len(top_1000) == len(set(top_1000)) == 1000


def test_top_100_is_a_subset_of_top_1000() -> None:
    assert set(profile_ports(PortProfile.TOP_100)) <= set(profile_ports(PortProfile.TOP_1000))


@pytest.mark.parametrize("profile", [PortProfile.TOP_100, PortProfile.TOP_1000])
def test_list_ports_are_valid_and_ascending(profile: PortProfile) -> None:
    ports = list(profile_ports(profile))

    assert all(1 <= port <= 65535 for port in ports)
    assert ports == sorted(ports)


def test_full_range_is_every_port_from_1_to_65535() -> None:
    ports = profile_ports(PortProfile.FULL)

    assert len(ports) == 65535
    assert (ports[0], ports[-1]) == (1, 65535)
    assert 0 not in ports


@pytest.mark.parametrize("port", [22, 25, 53, 80, 443, 445, 3306, 3389, 5432, 8080])
def test_short_list_holds_the_most_common_service_ports(port: int) -> None:
    assert port in profile_ports(PortProfile.TOP_100)


def test_profiles_are_named_as_in_the_specification() -> None:
    assert [profile.value for profile in PortProfile] == ["top-100", "top-1000", "full"]


# --- Nominal service names ---


@pytest.mark.parametrize(
    ("port", "name"),
    [(22, "ssh"), (25, "smtp"), (53, "domain"), (80, "http"), (443, "https"), (3306, "mysql")],
)
def test_service_name_is_the_iana_registration(port: int, name: str) -> None:
    assert service_name(port) == name


def test_port_without_a_registration_has_no_name() -> None:
    assert service_name(0) is None
    assert service_name(65536) is None
    # Most of the dynamic range is unassigned.
    assert service_name(61000) is None


# --- NFR-LIC-2 ---


def test_provenance_file_names_the_source_and_licence_of_every_data_file() -> None:
    data = resources.files("ndm.core").joinpath("data")
    provenance = data.joinpath("PROVENANCE.md").read_text(encoding="utf-8")
    files = sorted(entry.name for entry in data.iterdir() if entry.name != "PROVENANCE.md")

    assert files == ["iana-service-names.csv", "top-100-tcp.txt", "top-1000-tcp.txt"]
    table_rows = [line for line in provenance.splitlines() if line.startswith("| `")]
    for name in files:
        (row,) = [line for line in table_rows if f"`{name}`" in line]
        _, _file, source, licence, _ = (cell.strip() for cell in row.split("|"))
        assert source and licence, f"{name} has no source or no licence"
    assert "not derived from nmap" in provenance
