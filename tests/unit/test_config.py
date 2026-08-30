import pytest
from pydantic import ValidationError

from wp_modernizer.config.models import ApplicationConfig


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
        ApplicationConfig.parse_obj(raw)


def test_config_rejects_unknown_registries() -> None:
    raw = valid_config()
    raw["installations"]["i"]["source_server"] = "missing"
    with pytest.raises(ValidationError, match="servidor desconhecido"):
        ApplicationConfig.parse_obj(raw)
