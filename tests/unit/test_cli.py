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


def test_command_with_missing_dependency_is_blocked_before_run(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    empty_path = tmp_path / "empty-bin"
    empty_path.mkdir()
    result = CliRunner().invoke(
        cli,
        ["--config", str(config), "pipeline", "site", "--dry-run", "--json"],
        env={"PATH": str(empty_path)},
    )
    assert result.exit_code != 0
    assert "MYSQL_AVAILABLE" in result.output
    assert "PHP_AVAILABLE" in result.output
    assert "WPCLI_AVAILABLE" in result.output
    assert not (tmp_path / "state").exists()


def test_unknown_installation_is_operational_error(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    result = CliRunner().invoke(cli, ["--config", str(config), "plan", "missing"])
    assert result.exit_code != 0
    assert "Instalação desconhecida" in result.output


def test_mutable_command_rejects_unusable_state_directory_before_dependencies(
    tmp_path: Path,
) -> None:
    config = write_config(tmp_path)
    (tmp_path / "state").write_text("not a directory")

    result = CliRunner().invoke(cli, ["--config", str(config), "update", "site"])

    assert result.exit_code != 0
    assert "state_directory não está acessível" in result.output
