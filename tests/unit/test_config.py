from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from wp_modernizer.config import loader as config_loader
from wp_modernizer.config.loader import load_config
from wp_modernizer.config.models import (
    ApplicationConfig,
    DatabaseConfig,
    ManagedPluginConfig,
    ServerConfig,
)
from wp_modernizer.domain.errors import ConfigurationError


def valid_config():
    return {
        "allowed_app_roots": ["/home/apps"],
        "servers": {
            "s": {
                "host": "source.example.invalid",
                "environment": "production",
                "username_secret": "USER",
            }
        },
        "databases": {
            "d": {
                "host": "db.example.invalid",
                "username_secret": "DB_USER",
                "password_secret": "DB_PASS",
            }
        },
        "installations": {
            "i": {
                "source_server": "s",
                "source_environment": "production",
                "source_path": "/home/apps/example.org/wp-main/htdocs",
                "destination_path": "/home/apps/example.org/wp-test/htdocs",
                "destination_environment": "test",
                "allowed_database_endpoints": ["d"],
            }
        },
    }


def write_yaml(path: Path, content: object) -> Path:
    path.write_text(yaml.safe_dump(content), encoding="utf-8")
    return path


def load_with_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plugins: object
) -> ApplicationConfig:
    config_path = write_yaml(tmp_path / "config.yaml", valid_config())
    plugins_path = write_yaml(tmp_path / "plugins.yaml", plugins)
    monkeypatch.setattr(config_loader, "PLUGINS_CONFIG_PATH", plugins_path)
    return load_config(config_path)


def test_load_config_composes_server_config_and_managed_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_with_plugins(
        tmp_path,
        monkeypatch,
        {
            "managed_plugins": [
                {
                    "slug": "shared-plugin",
                    "repository": "https://example.invalid/shared-plugin.git",
                    "branch": "stable",
                    "strategy": "replace_from_git",
                    "dirty_policy": "skip",
                }
            ]
        },
    )

    assert config.servers["s"].host == "source.example.invalid"
    assert len(config.managed_plugins) == 1
    assert config.managed_plugins[0].slug == "shared-plugin"
    assert config.managed_plugins[0].branch == "stable"


def test_load_config_does_not_require_managed_plugins_in_server_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_with_plugins(tmp_path, monkeypatch, {"managed_plugins": []})

    assert config.managed_plugins == []


def test_load_config_rejects_managed_plugins_in_server_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = valid_config()
    raw["managed_plugins"] = []
    config_path = write_yaml(tmp_path / "config.yaml", raw)
    monkeypatch.setattr(config_loader, "PLUGINS_CONFIG_PATH", tmp_path / "plugins.yaml")

    with pytest.raises(ConfigurationError, match=r"(?s)config\.yaml.*managed_plugins"):
        load_config(config_path)


def test_missing_plugins_config_raises_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = write_yaml(tmp_path / "config.yaml", valid_config())
    monkeypatch.setattr(config_loader, "PLUGINS_CONFIG_PATH", tmp_path / "missing-plugins.yaml")

    with pytest.raises(ConfigurationError, match=r"ler plugins\.yaml"):
        load_config(config_path)


def test_unreadable_plugins_config_raises_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = write_yaml(tmp_path / "config.yaml", valid_config())
    plugins_path = tmp_path / "plugins.yaml"
    plugins_path.mkdir()
    monkeypatch.setattr(config_loader, "PLUGINS_CONFIG_PATH", plugins_path)

    with pytest.raises(ConfigurationError, match=r"ler plugins\.yaml"):
        load_config(config_path)


def test_invalid_plugins_yaml_raises_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = write_yaml(tmp_path / "config.yaml", valid_config())
    plugins_path = tmp_path / "plugins.yaml"
    plugins_path.write_text("managed_plugins: [", encoding="utf-8")
    monkeypatch.setattr(config_loader, "PLUGINS_CONFIG_PATH", plugins_path)

    with pytest.raises(ConfigurationError, match=r"YAML inválido em plugins\.yaml"):
        load_config(config_path)


@pytest.mark.parametrize("plugins", [[], {}, {"managed_plugins": "invalid"}])
def test_invalid_plugins_structure_raises_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plugins: object
) -> None:
    config_path = write_yaml(tmp_path / "config.yaml", valid_config())
    plugins_path = write_yaml(tmp_path / "plugins.yaml", plugins)
    monkeypatch.setattr(config_loader, "PLUGINS_CONFIG_PATH", plugins_path)

    with pytest.raises(ConfigurationError, match=r"plugins\.yaml"):
        load_config(config_path)


def test_plugins_config_preserves_managed_plugin_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ConfigurationError, match=r"(?s)plugins\.yaml.*slug"):
        load_with_plugins(
            tmp_path,
            monkeypatch,
            {"managed_plugins": [{"slug": "../escape", "repository": "repo"}]},
        )


def test_fixed_plugins_path_is_independent_of_current_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed_path = config_loader.PLUGINS_CONFIG_PATH
    monkeypatch.chdir(tmp_path)

    assert fixed_path.is_absolute()
    config = load_config(write_yaml(tmp_path / "config.yaml", valid_config()))
    assert config.managed_plugins


def test_config_rejects_production_destination() -> None:
    raw = valid_config()
    raw["installations"]["i"]["destination_environment"] = "production"
    with pytest.raises(ValidationError, match="deve ser test"):
        ApplicationConfig.model_validate(raw)


def test_config_rejects_unknown_registries() -> None:
    raw = valid_config()
    raw["installations"]["i"]["source_server"] = "missing"
    with pytest.raises(ValidationError, match="servidor desconhecido"):
        ApplicationConfig.model_validate(raw)


def test_config_rejects_unknown_database() -> None:
    raw = valid_config()
    raw["installations"]["i"]["allowed_database_endpoints"] = ["missing"]
    with pytest.raises(ValidationError, match="bancos desconhecidos"):
        ApplicationConfig.model_validate(raw)


def test_allowed_database_endpoints_rejects_production_endpoints() -> None:
    raw = valid_config()
    raw["databases"]["prod"] = {
        "host": "prod-db.example.invalid",
        "environment": "production",
        "username_secret": "PROD_DB_USER",
        "password_secret": "PROD_DB_PASS",
    }
    raw["installations"]["i"]["allowed_database_endpoints"] = ["prod"]
    with pytest.raises(ValidationError, match="somente destinos de TESTE"):
        ApplicationConfig.model_validate(raw)


def test_explicit_source_database_endpoint_is_separate_and_matches_environment() -> None:
    raw = valid_config()
    raw["databases"]["prod"] = {
        "host": "prod-db.example.invalid",
        "environment": "production",
        "username_secret": "PROD_DB_USER",
        "password_secret": "PROD_DB_PASS",
    }
    raw["installations"]["i"]["source_database_endpoint"] = "prod"
    config = ApplicationConfig.model_validate(raw)
    assert config.installations["i"].source_database_endpoint == "prod"
    assert config.installations["i"].allowed_database_endpoints == ["d"]

    raw["installations"]["i"]["source_database_endpoint"] = "d"
    with pytest.raises(ValidationError, match="source_environment"):
        ApplicationConfig.model_validate(raw)


def test_config_rejects_relative_allowed_root() -> None:
    raw = valid_config()
    raw["allowed_app_roots"] = ["relative/path"]
    with pytest.raises(ValidationError, match="caminhos absolutos"):
        ApplicationConfig.model_validate(raw)


def test_config_accepts_explicit_test_url() -> None:
    raw = valid_config()
    raw["installations"]["i"]["test_url"] = "https://qa.example.invalid/wordpress"
    config = ApplicationConfig.model_validate(raw)
    assert str(config.installations["i"].test_url) == "https://qa.example.invalid/wordpress"


def test_config_rejects_invalid_test_url() -> None:
    raw = valid_config()
    raw["installations"]["i"]["test_url"] = "not-a-url"
    with pytest.raises(ValidationError, match="test_url"):
        ApplicationConfig.model_validate(raw)


@pytest.mark.parametrize("legacy_value", [False, True])
def test_config_rejects_removed_allow_create_with_migration_message(
    legacy_value: bool,
) -> None:
    raw = valid_config()
    raw["databases"]["d"]["allow_create"] = legacy_value
    with pytest.raises(
        ValidationError,
        match=r"allow_create foi removido.*infraestrutura.*provisionamento prévio",
    ):
        ApplicationConfig.model_validate(raw)


def test_allow_create_is_not_part_of_public_database_schema() -> None:
    assert "allow_create" not in DatabaseConfig.model_json_schema()["properties"]


@pytest.mark.parametrize("field", ["database_override", "database_aliases"])
def test_config_rejects_blank_database_mapping_names(field: str) -> None:
    raw = valid_config()
    raw["installations"]["i"][field] = " " if field == "database_override" else [" "]
    with pytest.raises(ValidationError, match=field):
        ApplicationConfig.model_validate(raw)


def test_password_authentication_requires_password_secret() -> None:
    with pytest.raises(ValidationError, match="requer password_secret"):
        ServerConfig(
            host="source.example.invalid",
            environment="production",
            username_secret="USER",
            authentication="password",
        )


def test_password_authentication_accepts_secret_reference_and_rejects_private_key() -> None:
    server = ServerConfig(
        host="source.example.invalid",
        environment="production",
        username_secret="USER",
        authentication="password",
        password_secret="PASSWORD",
    )
    assert server.password_secret == "PASSWORD"
    with pytest.raises(ValidationError, match="não utiliza private_key"):
        ServerConfig(
            host="source.example.invalid",
            environment="production",
            username_secret="USER",
            authentication="password",
            password_secret="PASSWORD",
            private_key="/key",
        )


@pytest.mark.parametrize("slug", ["../escape", "nested/plugin", ".", ""])
def test_config_rejects_unsafe_plugin_slugs(slug: str) -> None:
    with pytest.raises(ValidationError, match="slug"):
        ManagedPluginConfig(slug=slug, repository="repo")
