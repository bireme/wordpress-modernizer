import re
from typing import Dict, Iterable, Mapping, Sequence, Tuple


class Redactor:
    MASK = "[REDACTED]"
    _assignment = re.compile(
        r"(?i)(password|passwd|pwd|token|secret)([\"']?\s*[:=]\s*[\"']?)([^\s,;\"']+)"
    )
    _wp_constant = re.compile(
        r"(?i)(define\s*\(\s*['\"](?:DB_PASSWORD|AUTH_KEY|SECURE_AUTH_KEY|LOGGED_IN_KEY|"
        r"NONCE_KEY|AUTH_SALT|SECURE_AUTH_SALT|LOGGED_IN_SALT|NONCE_SALT)['\"]\s*,\s*['\"])([^'\"]+)(['\"]\s*\))"
    )
    _connection = re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^/:\s]+:)([^@\s/]+)(@)")
    _mysql_flag = re.compile(r"(?i)(-p)([^\s]+)")

    def __init__(self, known_secrets: Iterable[str] = ()) -> None:
        self._known = tuple(value for value in known_secrets if value)

    def redact(self, value: str) -> str:
        result = value
        for secret in self._known:
            result = result.replace(secret, self.MASK)
        result = self._assignment.sub(
            lambda match: match.group(1) + match.group(2) + self.MASK, result
        )
        result = self._wp_constant.sub(
            lambda match: match.group(1) + self.MASK + match.group(3), result
        )
        result = self._connection.sub(
            lambda match: match.group(1) + self.MASK + match.group(3), result
        )
        result = self._mysql_flag.sub(lambda match: match.group(1) + self.MASK, result)
        return result

    def argv(self, values: Sequence[str]) -> Tuple[str, ...]:
        return tuple(self.redact(value) for value in values)

    def mapping(self, values: Mapping[str, str]) -> Dict[str, str]:
        return {
            key: self.MASK if self._sensitive_key(key) else self.redact(value)
            for key, value in values.items()
        }

    def value(self, value: object, key: str = "") -> object:
        """Redact nested structured data before it is serialized to a log file."""
        if key and self._sensitive_key(key):
            return self.MASK
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, Mapping):
            return {
                str(item_key): self.value(item, str(item_key)) for item_key, item in value.items()
            }
        if isinstance(value, (list, tuple, set, frozenset)):
            return [self.value(item) for item in value]
        return value

    @staticmethod
    def _sensitive_key(key: str) -> bool:
        lowered = key.lower()
        return any(
            part in lowered for part in ("password", "passwd", "token", "secret", "mysql_pwd")
        )
