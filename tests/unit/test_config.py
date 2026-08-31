import pytest
from pydantic import ValidationError

from wp_modernizer.config.models import ApplicationConfig, DatabaseConfig, ServerConfig


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
