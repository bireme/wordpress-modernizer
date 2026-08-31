from pathlib import Path
from typing import Any

import paramiko

from tests.fakes.core import FakeClock, FakeCommandRunner, FakeFileSystem, FakeProbe, health
from wp_modernizer.cli.main import build_service
from wp_modernizer.config.models import ApplicationConfig
from wp_modernizer.domain.enums import HealthStatus, Operation, RunStatus, StepStatus
from wp_modernizer.domain.models import PlannedStep, RunManifest
from wp_modernizer.infrastructure.mysql.adapter import MySQLAdapter
from wp_modernizer.infrastructure.runtime_operations import RuntimeOperations
from wp_modernizer.infrastructure.ssh import (
    FileTransferRouter,
    PasswordSFTPAdapter,
    RSyncSSHAdapter,
)
from wp_modernizer.infrastructure.state import JsonStateStore
from wp_modernizer.infrastructure.wpcli.adapter import WPCLIAdapter
from wp_modernizer.pipeline.runner import PipelineRunner
from wp_modernizer.pipeline.steps import OperationStep


class RecordingSecrets:
    def __init__(self) -> None:
        self.calls = []

    def get(self, reference: str) -> str:
        self.calls.append(reference)
        return {
            "SSH_USER": "ssh-user-must-not-leak",
            "SSH_PASSWORD": "ssh-password-must-not-leak",
            "DB_USER": "db-user-must-not-leak",
            "DB_PASSWORD": "db-password-must-not-leak",
        }[reference]


class RejectingSSHClient:
    def load_system_host_keys(self) -> None:
        pass

    def load_host_keys(self, filename: str) -> None:
        del filename

    def set_missing_host_key_policy(self, policy: Any) -> None:
        del policy

    def connect(self, **kwargs: Any) -> None:
        del kwargs
        raise paramiko.AuthenticationException("ssh-password-must-not-leak")

    def close(self) -> None:
        pass


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
    assert isinstance(operations._files, FileTransferRouter)
    assert isinstance(operations._files._transports["key"], RSyncSSHAdapter)
    assert isinstance(operations._files._transports["password"], PasswordSFTPAdapter)
    assert operations._files.get_server("source") is config.servers["source"]
    assert isinstance(operations._databases, MySQLAdapter)
    assert operations._databases.get_database("test-db") is config.databases["test-db"]
    assert service._probe._database is operations._databases
    assert service._probe._database_endpoints == {
        config.installations["site"].destination_path: ("test-db",)
    }
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


def test_composition_root_routes_password_server_to_sftp(tmp_path: Path) -> None:
    config = configured(tmp_path)
    config.servers["source"] = config.servers["source"].model_copy(
        update={"authentication": "password", "password_secret": "SSH_PASSWORD"}
    )
    service = build_service(config, runner=FakeCommandRunner(), secrets=RecordingSecrets())
    files = service._operations._files

    assert isinstance(files, FileTransferRouter)
    assert files.get_server("source").authentication == "password"
    assert isinstance(files._transports["password"], PasswordSFTPAdapter)


def test_password_authentication_failure_becomes_safe_failed_step(tmp_path: Path) -> None:
    config = configured(tmp_path)
    config.servers["source"] = config.servers["source"].model_copy(
        update={"authentication": "password", "password_secret": "SSH_PASSWORD"}
    )
    service = build_service(
        config,
        runner=FakeCommandRunner(),
        secrets=RecordingSecrets(),
        ssh_client_factory=RejectingSSHClient,
    )
    operations = service._operations
    context = {
        "run_id": "run-1",
        "installation_id": "site",
        "installation": config.installations["site"],
        "installations": config.installations,
        "planned_step": planned("copy_files"),
    }

    result = operations.execute("copy_files", context)

    assert result.status is StepStatus.FAILED
    assert "ssh-password-must-not-leak" not in result.message
    manifest = RunManifest("run-1", "site", Operation.MIGRATE, RunStatus.RUNNING, "now", False)
    store = JsonStateStore(tmp_path / "state-check")
    preserved = PipelineRunner(
        FakeProbe([health(HealthStatus.HEALTHY)]), store, FakeFileSystem(), FakeClock()
    ).run(
        manifest,
        config.installations["site"].destination_path,
        [OperationStep("copy_files", operations)],
        context,
    )

    assert preserved.status is RunStatus.UPDATE_FAILED_PRESERVED
    persisted = "".join(path.read_text() for path in (tmp_path / "state-check").rglob("*.json"))
    assert "ssh-password-must-not-leak" not in persisted
