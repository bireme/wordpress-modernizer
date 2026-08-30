from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from wp_modernizer.domain.enums import Environment
from wp_modernizer.domain.errors import UnsafeOperationError
from wp_modernizer.domain.models import MigrationTarget
from wp_modernizer.security.redaction import Redactor


@pytest.mark.parametrize(
    "text,secret",
    [
        ("password=hunter2", "hunter2"),
        ("mysql://user:hunter2@db.invalid/name", "hunter2"),
        ("mysql -phunter2 db", "hunter2"),
        ("token: abc123", "abc123"),
    ],
)
def test_redaction_removes_credentials(text: str, secret: str) -> None:
    redacted = Redactor().redact(text)
    assert secret not in redacted
    assert "[REDACTED]" in redacted


def test_known_secret_removed_from_exception_style_text() -> None:
    assert "very-private" not in Redactor(["very-private"]).redact("failure: very-private")


def test_sensitive_environment_mapping_is_redacted() -> None:
    assert Redactor().mapping({"MYSQL_PWD": "secret-value"}) == {"MYSQL_PWD": "[REDACTED]"}


@given(st.text(alphabet="0123456789", min_size=8, max_size=32))
def test_known_secrets_never_survive(value: str) -> None:
    assert Redactor([value]).redact("prefix " + value + " suffix") == ("prefix [REDACTED] suffix")


def test_production_destination_is_domain_error() -> None:
    with pytest.raises(UnsafeOperationError):
        MigrationTarget("site", Path("/safe/site"), Environment.PRODUCTION)
