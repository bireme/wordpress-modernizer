from pathlib import Path

from tests.fakes.core import FakeCommandRunner
from wp_modernizer.cli.main import build_service
from wp_modernizer.config.models import ApplicationConfig
from wp_modernizer.domain.enums import StepStatus
from wp_modernizer.domain.models import PlannedStep
from wp_modernizer.infrastructure.mysql.adapter import MySQLAdapter
from wp_modernizer.infrastructure.runtime_operations import RuntimeOperations
from wp_modernizer.infrastructure.ssh.adapter import RSyncSSHAdapter
from wp_modernizer.infrastructure.wpcli.adapter import WPCLIAdapter


class RecordingSecrets:
    def __init__(self) -> None:
        self.calls = []

    def get(self, reference: str) -> str:
        self.calls.append(reference)
        return {
            "SSH_USER": "ssh-user-must-not-leak",
            "DB_USER": "db-user-must-not-leak",
            "DB_PASSWORD": "db-password-must-not-leak",
        }[reference]


def configured(tmp_path: Path) -> ApplicationConfig:
    return ApplicationConfig.model_validate(
        {
            "state_directory": str(tmp_path / "state"),
            "allowed_app_roots": [str(tmp_path)],
            "servers": {
                "source": {
                    "host": "source.example.invalid",
                    "environment": "production",
                    "username_secret": "SSH_USER",
                }
            },
            "databases": {
                "test-db": {
                    "host": "db.example.invalid",
                    "environment": "test",
                    "username_secret": "DB_USER",
                    "password_secret": "DB_PASSWORD",
                }
            },
            "installations": {
                "site": {
                    "source_server": "source",
                    "source_environment": "production",
                    "source_path": "/remote/example.org/wp-main/htdocs",
                    "destination_path": str(tmp_path / "example.org/wp-test/htdocs"),
                    "destination_environment": "test",
                    "allowed_database_endpoints": ["test-db"],
                }
            },
        }
    )


def planned(name: str, excludes: tuple[Path, ...] = ()) -> PlannedStep:
    return PlannedStep(name, True, True, "probe", "recovery", "site", excludes)


def test_composition_root_wires_config_secrets_and_all_runtime_adapters(tmp_path: Path) -> None:
    config = configured(tmp_path)
    runner = FakeCommandRunner()
    secrets = RecordingSecrets()

    service = build_service(config, runner=runner, secrets=secrets)
    operations = service._operations

    assert isinstance(operations, RuntimeOperations)
    assert isinstance(operations._files, RSyncSSHAdapter)
    assert operations._files.get_server("source") is config.servers["source"]
    assert isinstance(operations._databases, MySQLAdapter)
    assert operations._databases.get_database("test-db") is config.databases["test-db"]
    assert isinstance(operations._wordpress, WPCLIAdapter)

    context = {
        "run_id": "run-1",
        "installation_id": "site",
        "installation": config.installations["site"],
        "installations": config.installations,
        "planned_step": planned("copy_files", (Path("child"), Path("*.sql"))),
    }
    result = operations.execute("copy_files", context)

    assert result.status is StepStatus.SUCCEEDED
    assert "SSH_USER" in secrets.calls
    assert runner.calls[0][0] == "rsync"
    assert "--exclude" in runner.calls[0]
    assert "ssh-user-must-not-leak" not in " ".join(runner.calls[0])

    operations._databases.list_schemas("test-db")
    assert {"DB_USER", "DB_PASSWORD"}.issubset(secrets.calls)
    assert not any("must-not-leak" in argument for call in runner.calls for argument in call)

    context["planned_step"] = planned("core_update")
    wp_result = operations.execute("core_update", context)
    assert wp_result.status is StepStatus.SUCCEEDED
    assert runner.calls[-1][-2:] == ("core", "update")
