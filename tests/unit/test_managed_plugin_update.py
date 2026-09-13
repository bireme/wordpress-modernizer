import subprocess
from dataclasses import replace

import pytest
from pydantic import ValidationError

from tests.fakes.core import FakeCommandResult
from tests.unit.test_managed_plugins import installation, parsed_installation
from wp_modernizer.config.models import ManagedPluginConfig
from wp_modernizer.domain.enums import ManagedPluginStatus
from wp_modernizer.domain.models import ManagedPlugin
from wp_modernizer.infrastructure.filesystem import LocalFileSystem
from wp_modernizer.infrastructure.managed_plugins import ManagedPluginRefresher


def git(path, *args):
    result = subprocess.run(  # noqa: S603
        ["/usr/bin/git", *args], cwd=path, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


class GitRunner:
    def __init__(self, fail=None):
        self.fail = fail
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append(argv)
        if self.fail and self.fail in argv:
            return FakeCommandResult(return_code=1)
        result = subprocess.run(  # noqa: S603
            argv, cwd=kwargs.get("cwd"), capture_output=True, text=True
        )
        return FakeCommandResult(result.returncode, result.stdout, result.stderr)


@pytest.fixture(params=["master", "main"])
def checkout(tmp_path, request):
    site, plugins = installation(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init")
    git(source, "symbolic-ref", "HEAD", f"refs/heads/{request.param}")
    git(source, "config", "user.email", "test@example.org")
    git(source, "config", "user.name", "Test")
    (source / "tracked").write_text("original\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "initial")
    target = plugins / "managed"
    git(plugins, "clone", str(source), str(target))
    git(target, "config", "user.email", "test@example.org")
    git(target, "config", "user.name", "Test")
    plugin = ManagedPlugin("managed", str(source), request.param, "update_from_git", "stash")
    return parsed_installation(tmp_path, site), source, target, plugin


def advance(source, conflict=False):
    (source / ("tracked" if conflict else "remote")).write_text("remote change\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "advance")


def refresh(checkout, runner=None, plugin=None):
    site, _, _, configured = checkout
    return ManagedPluginRefresher(LocalFileSystem(), runner or GitRunner()).refresh(
        site, [plugin or configured, replace(configured, slug="next")], "run-test"
    )


@pytest.mark.parametrize("dirty", [None, "tracked", "untracked"])
@pytest.mark.parametrize("updated", [False, True])
def test_update_and_stash_preserve_content(checkout, dirty, updated):
    _, source, target, _ = checkout
    if dirty:
        (target / dirty).write_text("local change\n")
    if updated:
        advance(source)
    result = refresh(checkout)[0]
    assert result.status is ManagedPluginStatus.REFRESHED
    assert result.changed is updated
    assert result.revision == git(source, "rev-parse", "HEAD")
    if dirty:
        assert (target / dirty).read_text() == "local change\n"
        assert "wp-modernizer run-test" in git(target, "stash", "list")


@pytest.mark.parametrize("failure", ["fetch", "merge", "conflict", "divergence"])
def test_failures_preserve_stash_and_stop(checkout, failure):
    _, source, target, _ = checkout
    if failure == "divergence":
        (target / "local-commit").write_text("local commit")
        git(target, "add", ".")
        git(target, "commit", "-m", "diverge")
    before = git(target, "rev-parse", "HEAD")
    (target / "tracked").write_text("local change\n")
    (target / "untracked").write_text("untracked data")
    advance(source, conflict=failure == "conflict")
    results = refresh(checkout, GitRunner(failure))
    assert len(results) == 1
    assert results[0].status is ManagedPluginStatus.FAILED_PRESERVED
    assert git(target, "show", "stash@{0}:tracked") == "local change"
    assert git(target, "show", "stash@{0}^3:untracked") == "untracked data"
    if failure == "conflict":
        assert "conflito" in results[0].message
        assert git(target, "rev-parse", "HEAD") == git(source, "rev-parse", "HEAD")
    else:
        assert git(target, "rev-parse", "HEAD") == before
    assert "recuperação manual" in results[0].message


@pytest.mark.parametrize("policy", ["abort", "skip"])
def test_dirty_policies(checkout, policy):
    _, _, target, configured = checkout
    (target / "tracked").write_text("preserve")
    result = refresh(checkout, plugin=replace(configured, dirty_policy=policy))[0]
    assert result.status is (
        ManagedPluginStatus.SKIPPED if policy == "skip" else ManagedPluginStatus.FAILED_PRESERVED
    )
    assert (target / "tracked").read_text() == "preserve"
    assert not git(target, "stash", "list")


@pytest.mark.parametrize("mismatch", ["remote", "branch", "detached"])
def test_rejects_mismatched_checkout_before_stash(checkout, mismatch):
    _, _, target, configured = checkout
    (target / "tracked").write_text("preserve")
    if mismatch == "remote":
        configured = replace(configured, repository="https://example.org/other.git")
    elif mismatch == "branch":
        configured = replace(configured, branch="other")
    else:
        git(target, "checkout", "--detach")
    result = refresh(checkout, plugin=configured)[0]
    assert result.status is ManagedPluginStatus.FAILED_PRESERVED
    assert (target / "tracked").read_text() == "preserve"
    assert not git(target, "stash", "list")


@pytest.mark.parametrize("policy", ["abort", "skip", "stash"])
def test_non_git_preserved(checkout, policy):
    site, _, target, configured = checkout
    old = target.parent / "old"
    old.mkdir()
    (old / "custom").write_text("preserve")
    result = ManagedPluginRefresher(LocalFileSystem(), GitRunner()).refresh(
        site, [replace(configured, slug="old", dirty_policy=policy)], "run-test"
    )[0]
    assert result.status is (
        ManagedPluginStatus.SKIPPED if policy == "skip" else ManagedPluginStatus.FAILED_PRESERVED
    )
    assert (old / "custom").read_text() == "preserve"


def test_initial_install_reuses_clone(checkout):
    site, source, target, configured = checkout
    runner = GitRunner()
    result = ManagedPluginRefresher(LocalFileSystem(), runner).refresh(
        site, [replace(configured, slug="new")], "run-test"
    )[0]
    assert result.status is ManagedPluginStatus.REFRESHED
    assert git(target.parent / "new", "rev-parse", "HEAD") == git(source, "rev-parse", "HEAD")
    assert sum(command[1] == "clone" for command in runner.calls) == 1


@pytest.mark.parametrize("strategy", ["replace_from_git", "update_from_git"])
@pytest.mark.parametrize("policy", ["abort", "skip", "stash"])
def test_config_accepts_strategies_and_policies(strategy, policy):
    config = ManagedPluginConfig(
        slug="managed", repository="repo", strategy=strategy, dirty_policy=policy
    )
    assert config.strategy == strategy
    assert config.dirty_policy == policy


@pytest.mark.parametrize("field", ["strategy", "dirty_policy"])
def test_config_rejects_unknown_values(field):
    with pytest.raises(ValidationError):
        ManagedPluginConfig.model_validate({"slug": "managed", "repository": "repo", field: "bad"})


def test_remote_normalization_is_conservative():
    identity = ManagedPluginRefresher._remote_identity
    assert identity("git@github.com:bireme/lis.git") == identity("https://github.com/bireme/lis")
    assert identity("https://github.com/other/lis.git") != identity("https://github.com/bireme/lis")


@pytest.mark.parametrize("unsafe", ["production", "symlink", "git-symlink"])
def test_update_safety(checkout, unsafe):
    from wp_modernizer.domain.enums import Environment
    from wp_modernizer.domain.errors import UnsafeOperationError

    site, _, target, configured = checkout
    runner = GitRunner()
    if unsafe == "production":
        site = replace(site, environment=Environment.PRODUCTION)
    elif unsafe == "symlink":
        (target.parent / "linked").symlink_to(target, target_is_directory=True)
        configured = replace(configured, slug="linked")
    else:
        metadata = target / ".git"
        metadata.rename(target.parent / "metadata")
        metadata.symlink_to(target.parent / "metadata", target_is_directory=True)
        result = ManagedPluginRefresher(LocalFileSystem(), runner).refresh(
            site, [configured], "run-test"
        )[0]
        assert result.status is ManagedPluginStatus.FAILED_PRESERVED
        assert runner.calls == []
        return
    with pytest.raises(UnsafeOperationError):
        ManagedPluginRefresher(LocalFileSystem(), runner).refresh(site, [configured], "run-test")
    assert runner.calls == []


def test_ignored_file_is_not_overwritten(checkout):
    _, source, target, _ = checkout
    (target / ".git" / "info" / "exclude").write_text("remote\n")
    (target / "remote").write_text("ignored local data")
    advance(source)
    result = refresh(checkout)[0]
    assert result.status is ManagedPluginStatus.FAILED_PRESERVED
    assert (target / "remote").read_text() == "ignored local data"


@pytest.mark.parametrize("failure", ["status", "stash"])
def test_failure_before_update_preserves_working_files(checkout, failure):
    _, _, target, _ = checkout
    (target / "tracked").write_text("local data")
    (target / "untracked").write_text("untracked data")
    result = refresh(checkout, GitRunner(failure))[0]
    assert result.status is ManagedPluginStatus.FAILED_PRESERVED
    assert (target / "tracked").read_text() == "local data"
    assert (target / "untracked").read_text() == "untracked data"


@pytest.mark.parametrize("policy", ["abort", "skip", "stash"])
def test_invalid_git_directory_preserved(checkout, policy):
    site, _, target, configured = checkout
    old = target.parent / "invalid"
    (old / ".git").mkdir(parents=True)
    (old / "custom").write_text("preserve")
    result = ManagedPluginRefresher(LocalFileSystem(), GitRunner()).refresh(
        site, [replace(configured, slug="invalid", dirty_policy=policy)], "run-test"
    )[0]
    assert result.status is (
        ManagedPluginStatus.SKIPPED if policy == "skip" else ManagedPluginStatus.FAILED_PRESERVED
    )
    assert (old / "custom").read_text() == "preserve"


def test_staged_changes_and_previous_stash_preserved(checkout):
    _, source, target, _ = checkout
    (target / "tracked").write_text("previous stash")
    git(target, "stash", "push", "-m", "previous")
    previous = git(target, "rev-parse", "refs/stash")
    (target / "tracked").write_text("staged change")
    git(target, "add", "tracked")
    advance(source)
    result = refresh(checkout)[0]
    assert result.status is ManagedPluginStatus.REFRESHED
    assert git(target, "show", ":tracked") == "staged change"
    assert git(target, "rev-parse", "stash@{1}") == previous
    assert (target / "tracked").read_text() == "staged change"


@pytest.mark.parametrize(
    "other",
    [
        "https://github.com/bireme/lis?repo=other",
        "https://github.com/bireme/lis#other",
        "ssh://other@github.com/bireme/lis",
        "https://github.com:8443/bireme/lis",
        "https://github.com/bireme/LIS",
    ],
)
def test_remote_differences_are_not_erased(other):
    identity = ManagedPluginRefresher._remote_identity
    assert identity(other) != identity("https://github.com/bireme/lis.git")
