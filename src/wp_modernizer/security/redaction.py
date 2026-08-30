import re
from typing import Dict, Iterable, Mapping, Sequence, Tuple


class Redactor:
    MASK = "[REDACTED]"
    _assignment = re.compile(r"(?i)(password|passwd|pwd|token|secret)(\s*[:=]\s*)([^\s,;]+)")
    _connection = re.compile(r"(?i)(mysql(?:\+\w+)?://[^:\s]+:)([^@\s]+)(@)")
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

    @staticmethod
    def _sensitive_key(key: str) -> bool:
        lowered = key.lower()
        return any(
            part in lowered for part in ("password", "passwd", "token", "secret", "mysql_pwd")
        )
