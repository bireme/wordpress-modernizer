import importlib
import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from wp_modernizer.cli.main import cli
from wp_modernizer.domain.enums import Capability, HealthStatus, RunStatus, StepStatus
from wp_modernizer.domain.models import RunManifest, StepResult


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
    logs = list((tmp_path / "state" / "logs").glob("*.log"))
    assert len(logs) == 1
    assert '"event": "run_failed"' in logs[0].read_text()


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


class FakeCliService:
    def __init__(self, *, dry_run: bool = False, fail: bool = False) -> None:
        self.dry_run = dry_run
        self.fail = fail

    def diagnose(self, installation_id: str):
        return {
            "installation": installation_id,
            "path": "/home/apps/example.org/wp-test/htdocs",
            "capabilities": [
                {
                    "capability": capability,
                    "available": available,
                    "detail": "very long technical detail that belongs only in the log",
                }
                for capability, available in (
                    (Capability.PHP_AVAILABLE, True),
                    (Capability.WPCLI_AVAILABLE, True),
                    (Capability.DATABASE_AVAILABLE, True),
                    (Capability.WP_CORE_DETECTED, True),
                    (Capability.WPCLI_FULL_BOOTSTRAP, True),
                )
            ],
            "health": HealthStatus.HEALTHY.value,
            "fatal_errors": (),
        }

    def execute(self, operation, installation_id, *, reporter, **kwargs):
        current = RunManifest(
            "run-1",
            installation_id,
            operation,
            RunStatus.RUNNING,
            "now",
            kwargs["dry_run"],
        )
        reporter.run_started(current, 2)
        statuses = (
            (StepStatus.VALIDATED, StepStatus.PLANNED)
            if current.dry_run
            else (StepStatus.EXECUTED, StepStatus.FAILED if self.fail else StepStatus.EXECUTED)
        )
        for index, (name, status) in enumerate(
            zip(("preflight", "core_update"), statuses, strict=True), 1
        ):
            reporter.step_started(name, index, 2)
            result = StepResult(name, status, status is StepStatus.EXECUTED, "short reason")
            current.steps.append(result)
            reporter.step_finished(result, index, 2)
            if status is StepStatus.FAILED:
                current.status = RunStatus.UPDATE_FAILED_PRESERVED
                current.failed_step = name
                reporter.run_failed(current, result.message)
                return current
        current.status = RunStatus.PLANNED if current.dry_run else RunStatus.EXECUTED
        current.health_after = HealthStatus.HEALTHY
        reporter.run_finished(current)
        return current


def invoke_with_service(tmp_path: Path, monkeypatch, service, arguments):
    cli_main = importlib.import_module("wp_modernizer.cli.main")
    monkeypatch.setattr(cli_main, "build_service", lambda config: service)
    return CliRunner().invoke(cli, ["--config", str(write_config(tmp_path)), *arguments])


def test_diagnose_human_is_short_and_log_has_full_details(tmp_path: Path, monkeypatch) -> None:
    result = invoke_with_service(tmp_path, monkeypatch, FakeCliService(), ["diagnose", "site"])
    assert result.exit_code == 0, result.output
    assert "✓ PHP available" in result.output
    assert "Health: HEALTHY" in result.output
    assert "very long technical detail" not in result.output
    assert "Log:" in result.output
    log = next((tmp_path / "state" / "logs").glob("*.log"))
    assert "very long technical detail" in log.read_text()


def test_diagnose_json_has_no_human_progress(tmp_path: Path, monkeypatch) -> None:
    result = invoke_with_service(
        tmp_path, monkeypatch, FakeCliService(), ["diagnose", "site", "--json"]
    )
    payload = json.loads(result.output)
    assert payload["health"] == "HEALTHY"
    assert "log_path" in payload


def test_pipeline_human_shows_progress_without_manifest_dump(tmp_path: Path, monkeypatch) -> None:
    result = invoke_with_service(tmp_path, monkeypatch, FakeCliService(), ["pipeline", "site"])
    assert result.exit_code == 0, result.output
    assert "[1/2] Checking destination" in result.output
    assert "[2/2] Updating WordPress core" in result.output
    assert "Pipeline completed successfully." in result.output
    assert "migration_plan" not in result.output
    assert "Log:" in result.output


def test_pipeline_dry_run_distinguishes_validated_and_planned(tmp_path: Path, monkeypatch) -> None:
    result = invoke_with_service(
        tmp_path, monkeypatch, FakeCliService(dry_run=True), ["pipeline", "site", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert "VALIDATED" in result.output
    assert "PLANNED" in result.output
    assert "2 steps: 1 validated, 1 planned, 0 failed." in result.output


def test_pipeline_failure_is_short_and_preserved(tmp_path: Path, monkeypatch) -> None:
    result = invoke_with_service(
        tmp_path, monkeypatch, FakeCliService(fail=True), ["pipeline", "site"]
    )
    assert result.exit_code == 2
    assert "FAILED" in result.output
    assert "State preserved for recovery." in result.output
    assert "Reason: short reason" in result.output


def test_pipeline_json_contains_only_valid_json(tmp_path: Path, monkeypatch) -> None:
    result = invoke_with_service(
        tmp_path, monkeypatch, FakeCliService(), ["pipeline", "site", "--json"]
    )
    payload = json.loads(result.output)
    assert payload["run_id"] == "run-1"
    assert "log_path" in payload
