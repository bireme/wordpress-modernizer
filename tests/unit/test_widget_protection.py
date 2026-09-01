from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.fakes.core import FakeClock, FakeFileSystem, FakeProbe, FakeStateStore, health
from wp_modernizer.application.service import ModernizerService
from wp_modernizer.config.models import ApplicationConfig
from wp_modernizer.domain.enums import (
    Environment,
    HealthStatus,
    Operation,
    RunStatus,
    StepStatus,
    WidgetEventType,
)
from wp_modernizer.domain.errors import UnsafeOperationError
from wp_modernizer.domain.models import PlannedStep, RunManifest
from wp_modernizer.domain.widgets import WidgetOption, WidgetSnapshot
from wp_modernizer.infrastructure.runtime_operations import RuntimeOperations


def option(name: str, value: bytes = b"reference", table: str = "wp_options") -> WidgetOption:
    return WidgetOption(table, name, value, "yes")


def snapshot(*options: WidgetOption) -> WidgetSnapshot:
    return WidgetSnapshot.from_options(options)


class Databases:
    def __init__(self, snapshots: list[WidgetSnapshot]) -> None:
        self.snapshots = snapshots
        self.restore_calls: list[tuple[str, str, WidgetSnapshot]] = []

    def get_database(self, endpoint_id: str):
        del endpoint_id
        return SimpleNamespace(environment=Environment.TEST)

    def list_schemas(self, endpoint_id: str):
        del endpoint_id
        return {"wp_portal_tst"}

    def snapshot_widgets(self, endpoint_id: str, database: str) -> WidgetSnapshot:
        del endpoint_id, database
        return self.snapshots.pop(0)

    def restore_widgets(
        self, endpoint_id: str, database: str, reference: WidgetSnapshot, run_id: str
    ) -> None:
        del run_id
        self.restore_calls.append((endpoint_id, database, reference))


class WordPress:
    def get_config(self, path: Path, name: str, run_id: str) -> str:
        del path, name, run_id
        return "wp_portal_tst"


class ExecutableWordPress(WordPress):
    def get_site_url(self, path: Path, run_id: str) -> str:
        del path, run_id
        return "https://portal.bireme.org"

    def is_multisite(self, path: Path, run_id: str) -> bool:
        del path, run_id
        return False

    def search_replace(self, *args, **kwargs) -> int:
        del args, kwargs
        return 1

    def update(self, path: Path, arguments, run_id: str) -> str:
        del path, arguments, run_id
        return "ok"


class SequentialIds:
    def __init__(self) -> None:
        self.value = 0

    def new(self) -> str:
        self.value += 1
        return f"run-{self.value}"


def context(manifest: RunManifest, *, restore: bool = False, environment=Environment.TEST):
    return {
        "run_id": manifest.run_id,
        "manifest": manifest,
        "restore_widgets": restore,
        "installation": SimpleNamespace(
            destination_environment=environment,
            destination_path=Path("/test/htdocs"),
            allowed_database_endpoints=["test-db"],
        ),
        "installations": {},
    }


def execute_validation(
    before: WidgetSnapshot,
    after: WidgetSnapshot,
    *,
    restore: bool = False,
    restored: WidgetSnapshot | None = None,
):
    databases = Databases([before, after, *([restored] if restored is not None else [])])
    operations = RuntimeOperations(
        SimpleNamespace(),
        databases,  # type: ignore[arg-type]
        WordPress(),  # type: ignore[arg-type]
        SimpleNamespace(),
    )
    manifest = RunManifest("run-1", "site", Operation.UPDATE, RunStatus.RUNNING, "now", False)
    runtime_context = context(manifest, restore=restore)
    runtime_context["planned_step"] = PlannedStep("snapshot", True, True, "", "", "site")
    captured = operations.execute("snapshot", runtime_context)
    runtime_context["planned_step"] = PlannedStep("widget_validation", True, True, "", "", "site")
    validated = operations.execute("widget_validation", runtime_context)
    return captured, validated, manifest, databases


def test_no_widget_change_succeeds_with_verifiable_zero_diff() -> None:
    reference = snapshot(option("sidebars_widgets"), option("widget_text"))
    captured, result, manifest, databases = execute_validation(reference, reference)
    assert captured.metrics == {"protected_options": 2.0}
    assert result.status is StepStatus.SUCCEEDED
    assert result.metrics == {"widget_differences": 0.0}
    assert manifest.widget_diff == []
    assert databases.restore_calls == []


@pytest.mark.parametrize(
    ("name", "expected_event"),
    [
        ("widget_text", WidgetEventType.WIDGET_OPTION_CHANGED),
        ("sidebars_widgets", WidgetEventType.SIDEBAR_MAPPING_CHANGED),
        ("template", WidgetEventType.WIDGET_OPTION_CHANGED),
        ("stylesheet", WidgetEventType.WIDGET_OPTION_CHANGED),
    ],
)
def test_protected_option_change_fails_without_automatic_restore(
    name: str, expected_event: WidgetEventType
) -> None:
    before = snapshot(option(name, b"before"))
    after = snapshot(option(name, b"after"))
    _, result, manifest, databases = execute_validation(before, after)
    assert result.status is StepStatus.FAILED
    assert manifest.widget_diff == [
        {
            "event_type": expected_event.value,
            "table": "wp_options",
            "option_name": name,
        }
    ]
    assert databases.restore_calls == []


def test_explicit_restore_restores_and_revalidates_reference_snapshot() -> None:
    before = snapshot(option("widget_text", b"before"))
    after = snapshot(option("widget_text", b"after"))
    _, result, manifest, databases = execute_validation(
        before, after, restore=True, restored=before
    )
    assert result.status is StepStatus.SUCCEEDED
    assert result.changed is True
    assert result.metrics["widget_differences"] == 0.0
    assert result.metrics["detected_widget_differences"] == 1.0
    assert manifest.widget_diff == []
    assert databases.restore_calls == [("test-db", "wp_portal_tst", before)]


def test_incomplete_explicit_restore_fails_and_preserves_remaining_diff() -> None:
    before = snapshot(option("widget_text", b"before"))
    after = snapshot(option("widget_text", b"after"))
    _, result, manifest, databases = execute_validation(
        before, after, restore=True, restored=after
    )

    assert result.status is StepStatus.FAILED
    assert result.metrics == {
        "widget_differences": 1.0,
        "detected_widget_differences": 1.0,
    }
    assert manifest.widget_diff[0]["option_name"] == "widget_text"
    assert databases.restore_calls == [("test-db", "wp_portal_tst", before)]


def test_widget_operations_are_rejected_for_production() -> None:
    databases = Databases([])
    operations = RuntimeOperations(
        SimpleNamespace(),
        databases,  # type: ignore[arg-type]
        WordPress(),  # type: ignore[arg-type]
        SimpleNamespace(),
    )
    manifest = RunManifest("run-1", "site", Operation.UPDATE, RunStatus.RUNNING, "now", False)
    runtime_context = context(manifest, environment=Environment.PRODUCTION)
    runtime_context["planned_step"] = PlannedStep("snapshot", True, True, "", "", "site")
    with pytest.raises(UnsafeOperationError, match="fora de TESTE"):
        operations.execute("snapshot", runtime_context)
    assert databases.restore_calls == []


def test_resume_reuses_persisted_reference_after_preserved_divergence() -> None:
    reference = snapshot(option("sidebars_widgets", b"reference"))
    divergent = snapshot(option("sidebars_widgets", b"changed"))
    databases = Databases([reference, divergent, reference])
    wordpress = ExecutableWordPress()
    config = ApplicationConfig.model_validate(
        {
            "allowed_app_roots": ["/home/apps"],
            "servers": {
                "source": {
                    "host": "source.example.invalid",
                    "environment": "production",
                    "username_secret": "USER",
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
                    "source_path": "/home/apps/example.org/wp-main/htdocs",
                    "destination_path": "/home/apps/example.org/wp-test/htdocs",
                    "destination_environment": "test",
                    "allowed_database_endpoints": ["test-db"],
                }
            },
        }
    )
    operations = RuntimeOperations(
        SimpleNamespace(),
        databases,  # type: ignore[arg-type]
        wordpress,  # type: ignore[arg-type]
        SimpleNamespace(),
    )
    state = FakeStateStore()
    service = ModernizerService(
        config,
        FakeProbe([health(HealthStatus.HEALTHY)]),
        state,
        FakeFileSystem(),
        FakeClock(),
        SequentialIds(),
        operations,
    )

    failed = service.execute(Operation.UPDATE, "site", dry_run=False)
    assert failed.status is RunStatus.UPDATE_FAILED_PRESERVED
    assert failed.failed_step == "widget_validation"
    assert failed.widget_snapshot == reference

    resumed = service.resume("site", failed.run_id, dry_run=False)
    assert resumed.status is RunStatus.SUCCEEDED
    assert resumed.widget_snapshot == reference
    assert resumed.widget_diff == []
    assert databases.snapshots == []


def test_resume_restores_preserved_divergence_only_when_explicitly_requested() -> None:
    reference = snapshot(option("widget_text", b"reference"))
    divergent = snapshot(option("widget_text", b"changed"))
    databases = Databases([reference, divergent, divergent, reference])
    wordpress = ExecutableWordPress()
    config = ApplicationConfig.model_validate(
        {
            "allowed_app_roots": ["/home/apps"],
            "servers": {
                "source": {
                    "host": "source.example.invalid",
                    "environment": "production",
                    "username_secret": "USER",
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
                    "source_path": "/home/apps/example.org/wp-main/htdocs",
                    "destination_path": "/home/apps/example.org/wp-test/htdocs",
                    "destination_environment": "test",
                    "allowed_database_endpoints": ["test-db"],
                }
            },
        }
    )
    operations = RuntimeOperations(
        SimpleNamespace(),
        databases,  # type: ignore[arg-type]
        wordpress,  # type: ignore[arg-type]
        SimpleNamespace(),
    )
    state = FakeStateStore()
    service = ModernizerService(
        config,
        FakeProbe([health(HealthStatus.HEALTHY)]),
        state,
        FakeFileSystem(),
        FakeClock(),
        SequentialIds(),
        operations,
    )

    failed = service.execute(Operation.UPDATE, "site", dry_run=False)
    resumed = service.resume(
        "site", failed.run_id, dry_run=False, restore_widgets=True
    )

    assert resumed.status is RunStatus.SUCCEEDED
    assert resumed.widget_snapshot == reference
    assert resumed.widget_diff == []
    assert databases.restore_calls == [("test-db", "wp_portal_tst", reference)]
