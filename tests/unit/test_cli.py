import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from wp_modernizer.cli.main import cli


def write_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "state_directory": str(tmp_path / "state"),
                "allowed_app_roots": ["/home/apps"],
                "servers": {
                    "source": {
                        "host": "source.example.invalid",
                        "environment": "production",
                        "username_secret": "USER",
                    }
                },
                "databases": {
                    "db": {
                        "host": "db.example.invalid",
                        "username_secret": "DB_USER",
                        "password_secret": "DB_PASSWORD",
                    }
                },
                "installations": {
                    "site": {
                        "source_server": "source",
                        "source_environment": "production",
                        "source_path": "/home/apps/example.org/wp-main/htdocs",
                        "destination_path": "/home/apps/example.org/wp-test/htdocs",
                        "destination_environment": "test",
                        "allowed_database_endpoints": ["db"],
                    }
                },
            }
        )
    )
    return path


def test_plan_json_is_read_only_and_machine_readable(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = CliRunner().invoke(cli, ["--config", str(config), "plan", "site", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["destination_environment"] == "test"
    assert payload["pending_operations"][0]["operation_type"] == "SEARCH_REPLACE"


def test_command_level_dry_run_executes_no_mutating_adapter(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = CliRunner().invoke(
        cli, ["--config", str(config), "pipeline", "site", "--dry-run", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "PLANNED"
    assert all(step["status"] == "PLANNED" for step in payload["steps"])


def test_unknown_installation_is_operational_error(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = CliRunner().invoke(cli, ["--config", str(config), "plan", "missing"])
    assert result.exit_code != 0
    assert "Instalação desconhecida" in result.output
