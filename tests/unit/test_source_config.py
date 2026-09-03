from pathlib import Path

import pytest

from tests.fakes.core import FakeCommandResult, FakeCommandRunner
from wp_modernizer.domain.errors import ConfigurationError, WordPressUnavailableError
from wp_modernizer.infrastructure.ssh.adapter import RSyncSSHAdapter
from wp_modernizer.infrastructure.ssh.source_config import parse_source_config

from .test_adapters import Secrets, password_server


def config(
    db_name: str = "'wordpress'", db_host: str = "'mysql:3306'", prefix: str = "'wp_'"
) -> str:
    return (
        "<?php\n"
        f"define ( 'DB_NAME' , {db_name} );\n"
        f'define("DB_HOST", {db_host});\n'
        f"$table_prefix = {prefix};\n"
        "define('DB_PASSWORD', 'never-report-this');\n"
        "define('AUTH_KEY', 'nor-this-salt');\n"
    )


def test_parses_single_quoted_database_name_host_and_table_prefix() -> None:
    parsed = parse_source_config(config())
    assert parsed.database_name == "wordpress"
    assert parsed.database_host == "mysql:3306"
    assert parsed.table_prefix == "wp_"


def test_parses_double_quoted_literals_and_spaces() -> None:
    parsed = parse_source_config(config('"wordpress"', '"mysql"', '"custom_2_"'))
    assert parsed.database_name == "wordpress"
    assert parsed.database_host == "mysql"
    assert parsed.table_prefix == "custom_2_"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (config().replace("define ( 'DB_NAME' , 'wordpress' );\n", ""), "DB_NAME não foi"),
        (config() + "define('DB_NAME', 'again');\n", "ambígua de DB_NAME"),
        (config(db_name="getenv('DB_NAME')"), "DB_NAME deve ser um literal"),
        (config(prefix="getenv('PREFIX')"), "table_prefix deve ser um literal"),
        (config(prefix="'wp-bad_'"), "table_prefix remoto"),
    ],
)
def test_rejects_missing_ambiguous_dynamic_and_unsafe_values(content: str, message: str) -> None:
    with pytest.raises(WordPressUnavailableError, match=message) as raised:
        parse_source_config(content)
    error = str(raised.value)
    assert "never-report-this" not in error
    assert "nor-this-salt" not in error


@pytest.mark.parametrize(
    "path", [Path("relative/site"), Path("/site/../secret"), Path("/bad\npath")]
)
def test_key_reader_rejects_unsafe_remote_paths_before_connecting(path: Path) -> None:
    server = password_server().model_copy(update={"authentication": "key", "password_secret": None})
    runner = FakeCommandRunner()
    adapter = RSyncSSHAdapter({"source": server}, Secrets(), runner)
    with pytest.raises(ConfigurationError, match="absoluto e seguro"):
        adapter.inspect_config("source", path, "run")
    assert runner.calls == []


def test_remote_read_failure_does_not_expose_returned_sensitive_output() -> None:
    server = password_server().model_copy(update={"authentication": "key", "password_secret": None})
    runner = FakeCommandRunner(
        [FakeCommandResult(return_code=1, stderr="DB_PASSWORD=never-report-this")]
    )
    adapter = RSyncSSHAdapter({"source": server}, Secrets(), runner)
    with pytest.raises(WordPressUnavailableError) as raised:
        adapter.inspect_config("source", Path("/source"), "run")
    assert "never-report-this" not in str(raised.value)
