import io
import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog
from pydantic import ValidationError

from ndm.core.logging import configure_logging
from ndm.core.settings import Settings


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("NDM_"):
            monkeypatch.delenv(name)


def test_defaults() -> None:
    settings = Settings()

    assert settings.database_url is None
    assert settings.migration_database_url is None
    assert settings.app_db_role == "ndm_app"
    assert settings.session_idle_timeout_seconds == 1800
    assert settings.project_scope_ceiling == 65_536
    assert settings.never_scan == []


def test_values_come_from_ndm_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NDM_DATABASE_URL", "postgresql+asyncpg://ndm_app:app-pw@postgres/ndm")
    monkeypatch.setenv("NDM_MIGRATION_DATABASE_URL", "postgresql+asyncpg://ndm:own-pw@postgres/ndm")
    monkeypatch.setenv("NDM_APP_DB_ROLE", "analyst_app")
    monkeypatch.setenv("NDM_SESSION_IDLE_TIMEOUT_SECONDS", "600")
    monkeypatch.setenv("NDM_PROJECT_SCOPE_CEILING", "1024")

    settings = Settings()

    assert settings.database_url is not None
    assert settings.database_url.get_secret_value().endswith("@postgres/ndm")
    assert settings.migration_database_url is not None
    assert settings.app_db_role == "analyst_app"
    assert settings.session_idle_timeout_seconds == 600
    assert settings.project_scope_ceiling == 1024


# --- Test switch (NFR-SEC-6) ---


def test_switch_is_off_unless_set_in_the_environment() -> None:
    assert Settings().allow_non_public_targets is False


def test_switch_is_on_when_set_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NDM_ALLOW_NON_PUBLIC_TARGETS", "true")

    assert Settings().allow_non_public_targets is True


def test_switch_cannot_be_set_from_code() -> None:
    settings = Settings(allow_non_public_targets=True)

    assert settings.allow_non_public_targets is False


def test_switch_cannot_be_set_from_a_dotenv_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    with open(".env", "w") as file:
        file.write("NDM_ALLOW_NON_PUBLIC_TARGETS=true\n")

    assert Settings().allow_non_public_targets is False


# --- Never-scan list ---


def test_never_scan_list_is_split_and_kept_as_raw_strings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NDM_NEVER_SCAN", "192.0.2.1, 198.51.100.0/24,not-an-address,")

    # Nothing is parsed or checked here; that needs ndm.core.scope.
    assert Settings().never_scan == ["192.0.2.1", "198.51.100.0/24", "not-an-address"]


# --- Phase 1B: broker, operator ceilings, scanner ---


def test_scan_pipeline_defaults() -> None:
    settings = Settings()

    assert settings.broker_url is None
    assert settings.global_rate_pps == 5000
    assert settings.max_concurrent_scans == 3
    assert settings.vantage_point == "default"
    assert settings.egress_address is None
    assert settings.source_ports == (61000, 61999)


def test_scan_pipeline_values_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NDM_BROKER_URL", "amqp://ndm:broker-pw@127.0.0.1:5672/")
    monkeypatch.setenv("NDM_GLOBAL_RATE_PPS", "2000")
    monkeypatch.setenv("NDM_MAX_CONCURRENT_SCANS", "1")
    monkeypatch.setenv("NDM_VANTAGE_POINT", "lab-1")
    monkeypatch.setenv("NDM_EGRESS_ADDRESS", "8.8.4.4")
    monkeypatch.setenv("NDM_SOURCE_PORT_RANGE", "62000-62499")

    settings = Settings()

    assert (settings.global_rate_pps, settings.max_concurrent_scans) == (2000, 1)
    assert (settings.vantage_point, str(settings.egress_address)) == ("lab-1", "8.8.4.4")
    assert settings.source_ports == (62000, 62499)
    # The broker password is a secret like the database passwords.
    assert "broker-pw" in settings.secret_values()
    assert "broker-pw" not in repr(settings)


# --- Enrichment and map (Phase 2) ---


def test_enrichment_and_map_defaults() -> None:
    settings = Settings()

    assert settings.reference_data_path == Path("/var/lib/ndm/reference")
    assert settings.maxmind_account_id is None
    assert settings.maxmind_license_key is None
    assert settings.geolite_update_interval_hours == 24
    assert settings.rir_update_interval_hours == 168
    # No registry's detailed objects are downloaded unless the operator names it.
    assert settings.rir_object_registries == []
    assert settings.rir_files_path is None
    assert settings.ptr_enabled is True
    assert settings.ptr_resolver is None
    assert settings.ptr_resolver_address is None
    # There is no default tile source (R6).
    assert settings.tile_url_template is None
    assert settings.tile_attribution is None


def test_enrichment_and_map_values_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NDM_REFERENCE_DATA_PATH", "/data/reference")
    monkeypatch.setenv("NDM_MAXMIND_ACCOUNT_ID", "123456")
    monkeypatch.setenv("NDM_MAXMIND_LICENSE_KEY", "licence-key-value")
    monkeypatch.setenv("NDM_GEOLITE_UPDATE_INTERVAL_HOURS", "12")
    monkeypatch.setenv("NDM_RIR_UPDATE_INTERVAL_HOURS", "24")
    monkeypatch.setenv("NDM_RIR_OBJECT_REGISTRIES", "ripe, apnic,ripe")
    monkeypatch.setenv("NDM_RIR_FILES_PATH", "/data/registry-files")
    monkeypatch.setenv("NDM_PTR_ENABLED", "false")
    monkeypatch.setenv("NDM_PTR_RESOLVER", "10.77.0.53:5353")
    monkeypatch.setenv("NDM_TILE_URL_TEMPLATE", "https://tiles.example/{z}/{x}/{y}.png?key=k")
    monkeypatch.setenv("NDM_TILE_ATTRIBUTION", "© Example Maps")

    settings = Settings()

    assert settings.reference_data_path == Path("/data/reference")
    assert settings.maxmind_account_id == "123456"
    assert settings.maxmind_license_key is not None
    assert settings.maxmind_license_key.get_secret_value() == "licence-key-value"
    assert settings.geolite_update_interval_hours == 12
    assert settings.rir_update_interval_hours == 24
    assert settings.rir_object_registries == ["apnic", "ripe"]
    assert settings.rir_files_path == Path("/data/registry-files")
    assert settings.ptr_enabled is False
    assert settings.ptr_resolver_address == ("10.77.0.53", 5353)
    assert settings.tile_url_template == "https://tiles.example/{z}/{x}/{y}.png?key=k"
    assert settings.tile_attribution == "© Example Maps"


def test_resolver_without_a_port_uses_port_53(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NDM_PTR_RESOLVER", "9.9.9.9")

    assert Settings().ptr_resolver_address == ("9.9.9.9", 53)


def test_an_empty_variable_counts_as_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose passes the optional variables the operator left out as empty strings."""
    for name in (
        "NDM_EGRESS_ADDRESS",
        "NDM_NEVER_SCAN",
        "NDM_ALLOW_NON_PUBLIC_TARGETS",
        "NDM_MAXMIND_ACCOUNT_ID",
        "NDM_MAXMIND_LICENSE_KEY",
        "NDM_RIR_OBJECT_REGISTRIES",
        "NDM_PTR_RESOLVER",
        "NDM_TILE_URL_TEMPLATE",
        "NDM_TILE_ATTRIBUTION",
    ):
        monkeypatch.setenv(name, "")

    settings = Settings()

    assert settings.egress_address is None
    assert settings.never_scan == []
    assert settings.allow_non_public_targets is False
    assert settings.maxmind_license_key is None
    assert settings.rir_object_registries == []
    assert settings.ptr_resolver is None
    assert settings.tile_url_template is None


@pytest.fixture
def restored_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers, root.level
    yield
    root.handlers, root.level = handlers, level
    structlog.reset_defaults()


def test_the_licence_key_reaches_no_log_line_when_logged_on_purpose(
    monkeypatch: pytest.MonkeyPatch, restored_logging: None
) -> None:
    monkeypatch.setenv("NDM_MAXMIND_ACCOUNT_ID", "123456")
    monkeypatch.setenv("NDM_MAXMIND_LICENSE_KEY", "licence-key-value")
    settings = Settings()
    assert settings.maxmind_license_key is not None
    key = settings.maxmind_license_key.get_secret_value()
    stream = io.StringIO()
    configure_logging(settings.secret_values(), stream=stream)

    structlog.get_logger().info("download", url=f"https://download.example/db?license_key={key}")
    logging.getLogger("httpx").warning("GET https://123456:%s@download.example/db", key)
    try:
        raise RuntimeError(f"download failed with key {key}")
    except RuntimeError:
        structlog.get_logger().exception("dataset_update_failed")

    written = stream.getvalue()
    assert written.count("\n") == 3
    assert "licence-key-value" not in written
    assert "[REDACTED]" in written


def test_the_licence_key_is_a_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NDM_MAXMIND_ACCOUNT_ID", "123456")
    monkeypatch.setenv("NDM_MAXMIND_LICENSE_KEY", "licence-key-value")

    settings = Settings()

    assert "licence-key-value" in settings.secret_values()
    assert "licence-key-value" not in repr(settings)


@pytest.mark.parametrize(
    "environment",
    [
        # The account and its key belong together.
        {"NDM_MAXMIND_LICENSE_KEY": "licence-key-value"},
        {"NDM_MAXMIND_ACCOUNT_ID": "123456"},
        {"NDM_MAXMIND_ACCOUNT_ID": "my-account", "NDM_MAXMIND_LICENSE_KEY": "k"},
        # ARIN and LACNIC objects cannot be downloaded; they come as files.
        {"NDM_RIR_OBJECT_REGISTRIES": "ripe,arin"},
        {"NDM_RIR_OBJECT_REGISTRIES": "RIPE"},
        {"NDM_PTR_RESOLVER": "resolver.example"},
        {"NDM_PTR_RESOLVER": "9.9.9"},
        {"NDM_PTR_RESOLVER": "9.9.9.9:0"},
        {"NDM_PTR_RESOLVER": "9.9.9.9:70000"},
        {"NDM_GEOLITE_UPDATE_INTERVAL_HOURS": "0"},
        {"NDM_RIR_UPDATE_INTERVAL_HOURS": "0"},
        # A tile source without the provider's attribution is not accepted (NFR-LIC-3).
        {"NDM_TILE_URL_TEMPLATE": "https://tiles.example/{z}/{x}/{y}.png"},
        {
            "NDM_TILE_URL_TEMPLATE": "https://tiles.example/{z}/{x}.png",
            "NDM_TILE_ATTRIBUTION": "© Example Maps",
        },
        {
            "NDM_TILE_URL_TEMPLATE": "tiles.example/{z}/{x}/{y}.png",
            "NDM_TILE_ATTRIBUTION": "© Example Maps",
        },
    ],
)
def test_invalid_enrichment_and_map_values_are_rejected(
    monkeypatch: pytest.MonkeyPatch, environment: dict[str, str]
) -> None:
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings()


def test_an_attribution_alone_configures_no_tile_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NDM_TILE_ATTRIBUTION", "© Example Maps")

    assert Settings().tile_url_template is None


# --- Validation ---


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("NDM_PROJECT_SCOPE_CEILING", "0"),
        ("NDM_SESSION_IDLE_TIMEOUT_SECONDS", "-1"),
        # The role name is put into SQL by the migrations.
        ("NDM_APP_DB_ROLE", 'app"; DROP TABLE audit_event; --'),
        ("NDM_GLOBAL_RATE_PPS", "0"),
        ("NDM_MAX_CONCURRENT_SCANS", "0"),
        # The vantage-point name becomes part of queue names.
        ("NDM_VANTAGE_POINT", "Lab 1"),
        ("NDM_VANTAGE_POINT", "a.b"),
        ("NDM_EGRESS_ADDRESS", "not-an-address"),
        ("NDM_SOURCE_PORT_RANGE", "61999-61000"),
        ("NDM_SOURCE_PORT_RANGE", "1000-2000"),
        ("NDM_SOURCE_PORT_RANGE", "61000:61999"),
        ("NDM_SOURCE_PORT_RANGE", "61000-70000"),
    ],
)
def test_invalid_values_are_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings()


# --- Secrets ---


def test_secret_values_lists_urls_and_the_passwords_inside_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_url = "postgresql+asyncpg://ndm_app:app%40pw@postgres/ndm"
    owner_url = "postgresql+asyncpg://ndm:own-pw@postgres/ndm"
    monkeypatch.setenv("NDM_DATABASE_URL", app_url)
    monkeypatch.setenv("NDM_MIGRATION_DATABASE_URL", owner_url)

    secrets = Settings().secret_values()

    assert set(secrets) == {app_url, "app%40pw", "app@pw", owner_url, "own-pw"}


def test_secrets_are_hidden_in_the_settings_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NDM_DATABASE_URL", "postgresql+asyncpg://ndm_app:app-pw@postgres/ndm")

    assert "app-pw" not in repr(Settings())
