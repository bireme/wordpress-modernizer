from __future__ import annotations

import re

from wp_modernizer.domain.errors import WordPressUnavailableError
from wp_modernizer.domain.models import SourceDatabaseConfiguration

_DEFINE_PREFIX = re.compile(
    r"\bdefine\s*\(\s*(?P<quote>['\"])(?P<name>DB_NAME|DB_HOST)(?P=quote)\s*,",
    re.IGNORECASE,
)
_PREFIX_ASSIGNMENT = re.compile(r"\$table_prefix\s*=", re.IGNORECASE)
_SINGLE_LITERAL = re.compile(r"'(?P<body>(?:\\['\\]|[^'\\])*)'", re.DOTALL)
_DOUBLE_LITERAL = re.compile(r'"(?P<body>(?:\\["\\]|[^"\\$])*)"', re.DOTALL)
_SAFE_PREFIX = re.compile(r"[A-Za-z0-9_]+")


def parse_source_config(content: str) -> SourceDatabaseConfiguration:
    """Extract the three permitted literals without interpreting PHP."""
    sanitized = _strip_php_comments(content)
    database_name = _one_define_literal(sanitized, "DB_NAME")
    database_host = _one_define_literal(sanitized, "DB_HOST")
    table_prefix = _one_prefix_literal(sanitized)
    if not database_name or any(character in database_name for character in "\r\n\x00"):
        raise WordPressUnavailableError("DB_NAME remoto não contém um literal seguro")
    if not database_host or any(character in database_host for character in "\r\n\x00"):
        raise WordPressUnavailableError("DB_HOST remoto não contém um literal seguro")
    if len(table_prefix) > 56 or not _SAFE_PREFIX.fullmatch(table_prefix):
        raise WordPressUnavailableError("table_prefix remoto é ausente ou inseguro")
    return SourceDatabaseConfiguration(database_name, database_host, table_prefix)


def _one_define_literal(content: str, requested_name: str) -> str:
    matches = [
        match
        for match in _DEFINE_PREFIX.finditer(content)
        if match.group("name").upper() == requested_name
    ]
    if not matches:
        raise WordPressUnavailableError(f"{requested_name} não foi encontrado no wp-config.php")
    if len(matches) != 1:
        raise WordPressUnavailableError(f"definição ambígua de {requested_name} no wp-config.php")
    expression = _expression_until_semicolon(content, matches[0].end())
    closing = expression.rfind(")")
    if closing < 0 or expression[closing + 1 :].strip():
        raise WordPressUnavailableError(f"{requested_name} deve ser um literal seguro")
    value_expression = expression[:closing].strip()
    return _decode_literal(value_expression, requested_name)


def _one_prefix_literal(content: str) -> str:
    matches = list(_PREFIX_ASSIGNMENT.finditer(content))
    if not matches:
        raise WordPressUnavailableError("table_prefix não foi encontrado no wp-config.php")
    if len(matches) != 1:
        raise WordPressUnavailableError("definição ambígua de table_prefix no wp-config.php")
    expression = _expression_until_semicolon(content, matches[0].end()).strip()
    return _decode_literal(expression, "table_prefix")


def _expression_until_semicolon(content: str, start: int) -> str:
    quote: str | None = None
    escaped = False
    for index in range(start, len(content)):
        character = content[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
        elif character in "'\"":
            quote = character
        elif character == ";":
            return content[start:index]
    raise WordPressUnavailableError("declaração incompleta no wp-config.php")


def _decode_literal(expression: str, field: str) -> str:
    single = _SINGLE_LITERAL.fullmatch(expression)
    double = _DOUBLE_LITERAL.fullmatch(expression)
    match = single or double
    if match is None:
        raise WordPressUnavailableError(f"{field} deve ser um literal seguro")
    body = match.group("body")
    quote = "'" if single else '"'
    return body.replace(f"\\{quote}", quote).replace("\\\\", "\\")


def _strip_php_comments(content: str) -> str:
    result: list[str] = []
    index = 0
    quote: str | None = None
    while index < len(content):
        character = content[index]
        following = content[index + 1] if index + 1 < len(content) else ""
        if quote is not None:
            result.append(character)
            if character == "\\" and following:
                result.append(following)
                index += 2
                continue
            if character == quote:
                quote = None
            index += 1
            continue
        if character in "'\"":
            quote = character
            result.append(character)
            index += 1
            continue
        if character == "/" and following == "*":
            end = content.find("*/", index + 2)
            if end < 0:
                raise WordPressUnavailableError("comentário incompleto no wp-config.php")
            result.extend("\n" for item in content[index : end + 2] if item == "\n")
            index = end + 2
            continue
        if (character == "/" and following == "/") or character == "#":
            end = content.find("\n", index)
            if end < 0:
                break
            result.append("\n")
            index = end + 1
            continue
        result.append(character)
        index += 1
    return "".join(result)
