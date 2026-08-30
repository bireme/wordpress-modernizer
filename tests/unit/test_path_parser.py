from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from wp_modernizer.domain.enums import Environment
from wp_modernizer.domain.errors import UnsafeOperationError
from wp_modernizer.domain.path_parser import InstallationPathParser


@pytest.fixture
def parser() -> InstallationPathParser:
    return InstallationPathParser([Path("/home/apps")])


def test_parses_traditional_installation(parser: InstallationPathParser) -> None:
    item = parser.parse("/home/apps/example.org/wp-el-salvador/htdocs", "site", Environment.TEST)
    assert item.domain == "example.org"
    assert item.instance_name == "el-salvador"
    assert item.relative_nested_path is None


def test_parses_multiple_nested_levels(parser: InstallationPathParser) -> None:
    item = parser.parse(
        "/home/apps/example.org/wp-main/htdocs/showcases/annual", "nested", Environment.TEST
    )
    assert item.relative_nested_path == Path("showcases/annual")
    assert item.document_root == Path("/home/apps/example.org/wp-main/htdocs")


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/home",
        "/home/apps",
        "/home/apps/x",
        "/home/apps/x/site/htdocs",
        "/tmp/x/wp-y/htdocs",
        "relative/wp-x/htdocs",
        "/home/apps/x/wp-y/../htdocs",
        "/home/apps/x/wp-/htdocs",
    ],
)
def test_rejects_invalid_or_dangerous_paths(parser: InstallationPathParser, path: str) -> None:
    with pytest.raises(UnsafeOperationError):
        parser.parse(path, "site", Environment.TEST)


def test_destructive_guard_rejects_production(parser: InstallationPathParser) -> None:
    item = parser.parse("/home/apps/example.org/wp-main/htdocs", "site", Environment.PRODUCTION)
    with pytest.raises(UnsafeOperationError, match="fora de TESTE"):
        parser.assert_safe_destructive_target(item.path, item)


def test_destructive_guard_requires_exact_path(parser: InstallationPathParser) -> None:
    item = parser.parse("/home/apps/example.org/wp-main/htdocs/nested", "site", Environment.TEST)
    with pytest.raises(UnsafeOperationError, match="exatamente"):
        parser.assert_safe_destructive_target(item.document_root, item)


@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=20))
def test_safe_nested_names_round_trip(name: str) -> None:
    if name.startswith(("-", "_")):
        return
    parser = InstallationPathParser([Path("/home/apps")])
    item = parser.parse(f"/home/apps/example.org/wp-main/htdocs/{name}", "site", Environment.TEST)
    assert str(item.relative_nested_path) == name
