from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.fakes.core import FakeCommandResult, FakeCommandRunner
from wp_modernizer.domain.enums import Environment, ManagedPluginStatus
from wp_modernizer.domain.errors import UnsafeOperationError
from wp_modernizer.domain.models import ManagedPlugin
from wp_modernizer.domain.path_parser import InstallationPathParser
from wp_modernizer.infrastructure.filesystem import LocalFileSystem
from wp_modernizer.infrastructure.managed_plugins import ManagedPluginRefresher


def plugin(*, slug: str = "managed", dirty_policy: str = "abort") -> ManagedPlugin:
    return ManagedPlugin(
        slug,
        "https://example.invalid/plugin.git",
        "stable",
        "replace_from_git",
        dirty_policy,
    )


def installation(tmp_path: Path) -> tuple[Path, Path]:
    site = tmp_path / "example.org" / "wp-test" / "htdocs"
    plugins = site / "wp-content" / "plugins"
    plugins.mkdir(parents=True)
    return site, plugins


def parsed_installation(tmp_path: Path, site: Path):
    return InstallationPathParser([tmp_path]).parse(str(site), "site", Environment.TEST)


def test_replace_from_git_replaces_only_validated_plugin_directory(tmp_path: Path) -> None:
    site, plugins = installation(tmp_path)
    target = plugins / "managed"
    target.mkdir()
    (target / "local.php").write_text("old")
    runner = FakeCommandRunner(
        [
            FakeCommandResult(stdout=""),
            FakeCommandResult(),
            FakeCommandResult(stdout="abc123\n"),
        ]
    )

    results = ManagedPluginRefresher(LocalFileSystem(), runner).refresh(
        parsed_installation(tmp_path, site), [plugin()], "run-1"
    )

    assert results[0].status is ManagedPluginStatus.REFRESHED
    assert results[0].revision == "abc123"
    assert target.is_dir() and not (target / "local.php").exists()
    assert runner.calls[0] == ("git", "status", "--porcelain", "--untracked-files=all")
    clone = runner.calls[1]
    assert clone[:7] == (
        "git",
        "clone",
        "--quiet",
        "--single-branch",
        "--branch",
        "stable",
        "--",
    )


def test_dirty_abort_preserves_and_stops_before_clone(tmp_path: Path) -> None:
    site, plugins = installation(tmp_path)
    target = plugins / "managed"
    target.mkdir()
    marker = target / "local.php"
    marker.write_text("preserve")
    runner = FakeCommandRunner([FakeCommandResult(stdout=" M local.php\n")])

    results = ManagedPluginRefresher(LocalFileSystem(), runner).refresh(
        parsed_installation(tmp_path, site),
        [plugin(), plugin(slug="second")],
        "run-1",
    )

    assert len(results) == 1
    assert results[0].status is ManagedPluginStatus.FAILED_PRESERVED
    assert "abort" in results[0].message
    assert marker.read_text() == "preserve"
    assert len(runner.calls) == 1


def test_dirty_skip_is_explicit_and_next_plugin_is_refreshed(tmp_path: Path) -> None:
    site, plugins = installation(tmp_path)
    dirty = plugins / "managed"
    dirty.mkdir()
    marker = dirty / "local.php"
    marker.write_text("preserve")
    runner = FakeCommandRunner(
        [
            FakeCommandResult(stdout="?? local.php\n"),
            FakeCommandResult(),
            FakeCommandResult(stdout="def456\n"),
        ]
    )

    results = ManagedPluginRefresher(LocalFileSystem(), runner).refresh(
        parsed_installation(tmp_path, site),
        [plugin(dirty_policy="skip"), plugin(slug="second")],
        "run-1",
    )

    assert [item.status for item in results] == [
        ManagedPluginStatus.SKIPPED,
        ManagedPluginStatus.REFRESHED,
    ]
    assert "skip explícito" in results[0].message
    assert marker.read_text() == "preserve"
    assert (plugins / "second").is_dir()


def test_non_git_existing_directory_is_conservatively_dirty(tmp_path: Path) -> None:
    site, plugins = installation(tmp_path)
    target = plugins / "managed"
    target.mkdir()
    runner = FakeCommandRunner([FakeCommandResult(return_code=128)])

    result = ManagedPluginRefresher(LocalFileSystem(), runner).refresh(
        parsed_installation(tmp_path, site), [plugin(dirty_policy="skip")], "run-1"
    )[0]

    assert result.status is ManagedPluginStatus.SKIPPED
    assert "não é um checkout Git verificável" in result.message
    assert target.exists()


def test_clone_failure_preserves_existing_clean_checkout(tmp_path: Path) -> None:
    site, plugins = installation(tmp_path)
    target = plugins / "managed"
    target.mkdir()
    marker = target / "version.php"
    marker.write_text("old")
    runner = FakeCommandRunner([FakeCommandResult(stdout=""), FakeCommandResult(return_code=128)])

    result = ManagedPluginRefresher(LocalFileSystem(), runner).refresh(
        parsed_installation(tmp_path, site), [plugin()], "run-1"
    )[0]

    assert result.status is ManagedPluginStatus.FAILED_PRESERVED
    assert marker.read_text() == "old"
    assert not list(plugins.glob(".wp-modernizer-*"))


def test_symlink_target_is_rejected_before_any_command_or_removal(tmp_path: Path) -> None:
    site, plugins = installation(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (plugins / "managed").symlink_to(outside, target_is_directory=True)
    runner = FakeCommandRunner()

    with pytest.raises(UnsafeOperationError, match="link simbólico"):
        ManagedPluginRefresher(LocalFileSystem(), runner).refresh(
            parsed_installation(tmp_path, site), [plugin()], "run-1"
        )

    assert outside.exists()
    assert runner.calls == []


def test_adapter_rejects_non_test_installation_before_any_command(tmp_path: Path) -> None:
    site, _ = installation(tmp_path)
    production = replace(parsed_installation(tmp_path, site), environment=Environment.PRODUCTION)
    runner = FakeCommandRunner()

    with pytest.raises(UnsafeOperationError, match="fora de TESTE"):
        ManagedPluginRefresher(LocalFileSystem(), runner).refresh(production, [plugin()], "run-1")

    assert runner.calls == []
