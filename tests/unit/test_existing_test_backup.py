from pathlib import Path
from types import SimpleNamespace

from wp_modernizer.domain.enums import Environment, StepStatus
from wp_modernizer.domain.errors import InfrastructureError
from wp_modernizer.domain.models import PlannedStep
from wp_modernizer.domain.path_parser import InstallationPathParser
from wp_modernizer.infrastructure.filesystem import LocalFileSystem
from wp_modernizer.infrastructure.runtime_operations import RuntimeOperations


class Files:
    def __init__(self) -> None:
        self.calls = []

    def get_server(self, server_id):
        return SimpleNamespace(environment=Environment.PRODUCTION)

    def copy_from(self, server_id, source, destination_parent, excludes, run_id):
        self.calls.append((server_id, source, destination_parent, excludes, run_id))
        destination = destination_parent / source.name
        destination.mkdir(parents=True)
        (destination / "new.txt").write_text("new")
        return 1


def installation(path: Path):
    return SimpleNamespace(
        destination_environment=Environment.TEST,
        destination_path=path,
        source_server="source",
        source_environment=Environment.PRODUCTION,
        source_path=Path("/remote/example.org/wp-prod/htdocs"),
    )


def step(name: str, installation_id: str = "site") -> PlannedStep:
    return PlannedStep(name, True, True, "", "", installation_id)


def runtime(root: Path, filesystem=None, files=None):
    return RuntimeOperations(
        files or Files(),
        SimpleNamespace(),
        SimpleNamespace(),
        InstallationPathParser([root]),
        filesystem=filesystem or LocalFileSystem(),
    )


def test_replace_existing_creates_verified_backup_then_replaces(tmp_path: Path) -> None:
    destination = tmp_path / "example.org/wp-test/htdocs"
    destination.mkdir(parents=True)
    (destination / "old.txt").write_text("preserve me")
    current = installation(destination)
    recovery: dict[str, dict[str, str]] = {}
    operations = runtime(tmp_path)
    context = {
        "run_id": "run-1",
        "installation": current,
        "installations": {},
        "replace_existing": True,
        "recovery_data": recovery,
        "planned_step": step("backup_existing_test"),
    }

    backed_up = operations.execute("backup_existing_test", context)
    backup = Path(recovery["site"]["backup_path"])

    assert backed_up.status is StepStatus.SUCCEEDED
    assert backup == tmp_path / "example.org/wp-test/.wp-modernizer-backups/run-1/site"
    assert backup not in destination.parents and destination not in backup.parents
    assert (backup / "old.txt").read_text() == "preserve me"
    assert operations._filesystem.verify_backup(backup, recovery["site"]["backup_fingerprint"])
    assert (backup / "old.txt").stat().st_mode & 0o222 == 0

    context["planned_step"] = step("copy_files")
    copied = operations.execute("copy_files", context)

    assert copied.status is StepStatus.SUCCEEDED
    assert not (destination / "old.txt").exists()
    assert (destination / "new.txt").read_text() == "new"
    assert (backup / "old.txt").read_text() == "preserve me"


def test_existing_destination_without_replace_is_preserved(tmp_path: Path) -> None:
    destination = tmp_path / "example.org/wp-test/htdocs"
    destination.mkdir(parents=True)
    marker = destination / "old.txt"
    marker.write_text("old")
    result = runtime(tmp_path).execute(
        "backup_existing_test",
        {
            "run_id": "run-1",
            "installation": installation(destination),
            "installations": {},
            "replace_existing": False,
            "recovery_data": {},
            "planned_step": step("backup_existing_test"),
        },
    )
    assert result.status is StepStatus.FAILED
    assert marker.read_text() == "old"


def test_backup_failure_never_removes_or_overwrites_existing_test(tmp_path: Path) -> None:
    class FailingBackup(LocalFileSystem):
        def create_immutable_backup(self, source, destination):
            raise InfrastructureError("storage unavailable")

    destination = tmp_path / "example.org/wp-test/htdocs"
    destination.mkdir(parents=True)
    marker = destination / "old.txt"
    marker.write_text("old")
    files = Files()
    result = runtime(tmp_path, FailingBackup(), files).execute(
        "backup_existing_test",
        {
            "run_id": "run-1",
            "installation": installation(destination),
            "installations": {},
            "replace_existing": True,
            "recovery_data": {},
            "planned_step": step("backup_existing_test"),
        },
    )
    assert result.status is StepStatus.FAILED
    assert marker.read_text() == "old"
    assert files.calls == []


def test_backup_refuses_symlink_destination(tmp_path: Path) -> None:
    real = tmp_path / "example.org/wp-test/real"
    real.mkdir(parents=True)
    destination = tmp_path / "example.org/wp-test/htdocs"
    destination.symlink_to(real, target_is_directory=True)
    try:
        runtime(tmp_path).execute(
            "backup_existing_test",
            {
                "run_id": "run-1",
                "installation": installation(destination),
                "installations": {},
                "replace_existing": True,
                "recovery_data": {},
                "planned_step": step("backup_existing_test"),
            },
        )
    except Exception as exc:
        assert "link simbólico" in str(exc)
    else:
        raise AssertionError("o symlink deveria ter sido recusado")


def test_nested_installation_backup_stays_outside_parent_document_root(tmp_path: Path) -> None:
    destination = tmp_path / "example.org/wp-test/htdocs/nested"
    destination.mkdir(parents=True)
    (destination / "old.txt").write_text("old")
    recovery: dict[str, dict[str, str]] = {}
    result = runtime(tmp_path).execute(
        "backup_existing_test",
        {
            "run_id": "run-nested",
            "installation": installation(destination),
            "installations": {},
            "replace_existing": True,
            "recovery_data": recovery,
            "planned_step": step("backup_existing_test", "nested"),
        },
    )
    backup = Path(recovery["nested"]["backup_path"])
    assert result.status is StepStatus.SUCCEEDED
    assert destination.parents[0] not in backup.parents
    assert backup == tmp_path / "example.org/wp-test/.wp-modernizer-backups/run-nested/nested"


def test_backup_refuses_symlink_in_backup_storage_path(tmp_path: Path) -> None:
    destination = tmp_path / "example.org/wp-test/htdocs"
    destination.mkdir(parents=True)
    (destination / "old.txt").write_text("old")
    outside = tmp_path / "outside"
    outside.mkdir()
    (destination.parent / ".wp-modernizer-backups").symlink_to(outside, target_is_directory=True)

    result = runtime(tmp_path).execute(
        "backup_existing_test",
        {
            "run_id": "run-1",
            "installation": installation(destination),
            "installations": {},
            "replace_existing": True,
            "recovery_data": {},
            "planned_step": step("backup_existing_test"),
        },
    )

    assert result.status is StepStatus.FAILED
    assert "link simbólico" in result.message
    assert (destination / "old.txt").read_text() == "old"
    assert list(outside.iterdir()) == []
