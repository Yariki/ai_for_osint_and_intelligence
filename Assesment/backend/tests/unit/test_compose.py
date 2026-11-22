"""The Compose file, read as data: what is mounted and connected where.

These properties are acceptance criteria, and a mistake in them is silent
until someone starts the stack.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parents[3] / "deploy"


@pytest.fixture(scope="module")
def services() -> dict[str, dict[str, Any]]:
    compose: dict[str, Any] = yaml.safe_load((DEPLOY / "compose.yaml").read_text())
    loaded: dict[str, dict[str, Any]] = compose["services"]
    return loaded


def mounts(service: dict[str, Any]) -> list[str]:
    """The source of every volume a service mounts."""
    return [str(volume).split(":")[0] for volume in service.get("volumes", [])]


def test_the_reference_data_volume_is_mounted_by_the_orchestrator_only(
    services: dict[str, dict[str, Any]],
) -> None:
    users = [name for name, service in services.items() if "reference-data" in mounts(service)]

    assert users == ["orchestrator"]


def test_only_the_orchestrator_gets_the_licence_key(services: dict[str, dict[str, Any]]) -> None:
    users = [
        name
        for name, service in services.items()
        if any("MAXMIND" in key for key in service.get("environment", {}))
    ]

    assert users == ["orchestrator"]


def test_the_orchestrator_can_reach_the_internet_and_the_api_and_the_database_cannot(
    services: dict[str, dict[str, Any]],
) -> None:
    # "internal" has no route to the outside; "egress" has one.
    assert services["orchestrator"]["networks"] == ["internal", "egress"]
    for name in ("api", "postgres", "migrate"):
        assert services[name]["networks"] == ["internal"], name


def test_only_the_proxy_publishes_a_port_to_the_outside(
    services: dict[str, dict[str, Any]],
) -> None:
    published = {name: service["ports"] for name, service in services.items() if "ports" in service}

    assert published == {"proxy": ["443:8443"], "rabbitmq": ["127.0.0.1:5672:5672"]}


def test_every_variable_the_stack_reads_is_in_the_example_file() -> None:
    read = set(re.findall(r"\$\{(NDM_[A-Z0-9_]+)", (DEPLOY / "compose.yaml").read_text()))
    example = (DEPLOY / ".env.example").read_text()

    assert read
    assert [name for name in sorted(read) if name not in example] == []


# --- Release: every image has an exact version (TASK-072) --------------------------

VERSIONS = DEPLOY / "release" / "VERSIONS.md"
OWN_IMAGES = ("ndm-backend", "ndm-scanner", "ndm-proxy", "ndm-scanner-netinit")


def resolved(reference: str) -> str:
    """An image reference with each ${NAME:-default} replaced by its default."""
    return re.sub(r"\$\{[A-Z_]+:-([^}]*)\}", r"\1", reference)


def release() -> str:
    found = re.search(r"^Release: `([^`]+)`$", VERSIONS.read_text(), re.MULTILINE)
    assert found, "VERSIONS.md names no release"
    return found.group(1)


def listed_images() -> set[str]:
    """The images of the stack as VERSIONS.md lists them: name:version."""
    section = VERSIONS.read_text().split("## Images of the stack")[1].split("\n## ")[0]
    rows = re.findall(r"^\| `([^`]+)` \| `([^`]+)` \|", section, re.MULTILINE)
    return {f"{name}:{version}" for name, version in rows}


def test_every_image_of_the_stack_has_an_exact_version(
    services: dict[str, dict[str, Any]],
) -> None:
    for name, service in services.items():
        image = resolved(service["image"])
        repository, _, tag = image.rpartition(":")
        assert repository and tag, f"{name}: {image} has no tag"
        assert tag not in ("latest", "local", "dev"), f"{name}: {image}"
        assert "$" not in image, f"{name}: {image} depends on a variable without a default"


def test_the_projects_own_images_carry_the_release_and_are_built_with_it(
    services: dict[str, dict[str, Any]],
) -> None:
    version = release()
    own = {name: s for name, s in services.items() if s["image"].startswith("ndm-")}
    assert {resolved(s["image"]).split(":")[0] for s in own.values()} == set(OWN_IMAGES)
    for name, service in own.items():
        assert resolved(service["image"]).endswith(f":{version}"), name
        args = service["build"]["args"]
        assert resolved(args["NDM_VERSION"]) == version, name


def test_versions_md_lists_exactly_the_images_of_the_stack(
    services: dict[str, dict[str, Any]],
) -> None:
    in_compose = {resolved(service["image"]) for service in services.values()}

    assert listed_images() == in_compose


def test_every_base_image_is_pinned_and_listed() -> None:
    text = VERSIONS.read_text()
    dockerfiles = [
        DEPLOY / "Dockerfile.backend",
        DEPLOY / "Dockerfile.scanner",
        DEPLOY / "Dockerfile.proxy",
        DEPLOY / "scanner-netinit" / "Dockerfile",
    ]
    for dockerfile in dockerfiles:
        source = dockerfile.read_text()
        bases = re.findall(r"^FROM (\S+)", source, re.MULTILINE)
        bases += re.findall(r"COPY --from=(\S+:\S+) ", source)
        for base in bases:
            name, _, tag = base.rpartition(":")
            assert name and tag and tag != "latest", f"{dockerfile.name}: {base}"
            assert f"| `{name}` | `{tag}` |" in text, f"{base} is not in VERSIONS.md"
        assert 'org.opencontainers.image.version="${NDM_VERSION}"' in source, dockerfile.name


# --- Posture of every service (checks 5.8 and 5.9, as declared) --------------------

# One-shot jobs: they run to the end and exit; a health check means nothing for them.
ONE_SHOT = {"migrate", "scanner-netinit"}


def test_every_long_running_service_has_a_health_check(
    services: dict[str, dict[str, Any]],
) -> None:
    for name, service in services.items():
        if name in ONE_SHOT:
            assert service.get("restart") == "no", name
        else:
            assert "test" in service.get("healthcheck", {}), name


def test_every_service_has_a_memory_and_a_cpu_limit(services: dict[str, dict[str, Any]]) -> None:
    for name, service in services.items():
        assert service.get("mem_limit") and service.get("cpus"), name


def test_every_service_drops_all_capabilities_and_adds_at_most_one(
    services: dict[str, dict[str, Any]],
) -> None:
    added = {}
    for name, service in services.items():
        assert service.get("cap_drop") == ["ALL"], name
        added[name] = service.get("cap_add", [])
    # The scanner's raw sockets, and the firewall rules of the one-shot netinit job.
    assert {name: caps for name, caps in added.items() if caps} == {
        "scanner": ["NET_RAW"],
        "scanner-netinit": ["NET_ADMIN"],
    }


def test_no_new_privileges_everywhere_but_the_scanner(
    services: dict[str, dict[str, Any]],
) -> None:
    without = [
        name
        for name, service in services.items()
        if "no-new-privileges:true" not in service.get("security_opt", [])
    ]
    # The scanner gets NET_RAW from a file capability, which no-new-privileges switches off.
    assert without == ["scanner"]


def test_root_file_systems_are_read_only_where_possible(
    services: dict[str, dict[str, Any]],
) -> None:
    writable = [name for name, service in services.items() if not service.get("read_only")]
    # RabbitMQ writes its node files outside its data volume.
    assert writable == ["rabbitmq"]


def test_services_run_as_their_own_users(services: dict[str, dict[str, Any]]) -> None:
    # The project's images set a numeric USER; the two third-party ones are given theirs here.
    images_with_user = {"ndm-backend", "ndm-scanner", "ndm-proxy"}
    for name, service in services.items():
        image = resolved(service["image"]).split(":")[0]
        if image in images_with_user:
            continue
        if name == "scanner-netinit":
            # Root, with NET_ADMIN only, to change the host firewall once, then it exits.
            continue
        assert service.get("user") not in (None, "root", "0"), name
    for dockerfile in ("Dockerfile.backend", "Dockerfile.scanner", "Dockerfile.proxy"):
        users = re.findall(r"^USER (\S+)", (DEPLOY / dockerfile).read_text(), re.MULTILINE)
        assert users and users[-1] not in ("root", "0"), dockerfile


def test_the_database_image_installs_no_extension_of_its_own(
    services: dict[str, dict[str, Any]],
) -> None:
    """The image's 10_postgis.sh would add topology and the TIGER geocoder."""
    volumes = services["postgres"]["volumes"]

    assert "./postgres/no-postgis-init.sh:/docker-entrypoint-initdb.d/10_postgis.sh:ro" in volumes
    assert (DEPLOY / "postgres" / "no-postgis-init.sh").stat().st_mode & 0o111


def test_the_python_services_without_signal_handlers_run_under_an_init(
    services: dict[str, dict[str, Any]],
) -> None:
    """As process 1 they would ignore SIGTERM and be killed after the stop timeout."""
    assert services["orchestrator"].get("init") is True
    assert services["scanner"].get("init") is True
