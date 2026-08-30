from dataclasses import dataclass
from typing import Dict, Iterable, Protocol, Sequence, Set, Tuple

from .enums import DatabaseLocationStatus
from .errors import AmbiguousDatabaseError, DatabaseNotFoundError


class DatabaseNamingStrategy(Protocol):
    def candidates(self, source_name: str, aliases: Sequence[str]) -> Tuple[str, ...]: ...


class SuffixDatabaseNamingStrategy:
    def __init__(self, test_suffix: str = "test") -> None:
        self._suffix = test_suffix

    def candidates(self, source_name: str, aliases: Sequence[str]) -> Tuple[str, ...]:
        base = source_name.rsplit("_", 1)[0] if "_" in source_name else source_name
        values = [f"{base}_{self._suffix}", *aliases]
        return tuple(dict.fromkeys(value for value in values if value))


class SchemaReader(Protocol):
    def list_schemas(self, endpoint_id: str) -> Set[str]: ...


@dataclass(frozen=True)
class DatabaseLocation:
    status: DatabaseLocationStatus
    endpoint_id: str
    database_name: str


class DatabaseLocator:
    def __init__(self, reader: SchemaReader, naming: DatabaseNamingStrategy) -> None:
        self._reader = reader
        self._naming = naming

    def locate(
        self,
        source_name: str,
        aliases: Sequence[str],
        endpoint_ids: Iterable[str],
        overrides: Dict[str, str],
        installation_id: str,
    ) -> DatabaseLocation:
        candidates = (
            (overrides[installation_id],)
            if installation_id in overrides
            else self._naming.candidates(source_name, aliases)
        )
        matches = [
            (endpoint, candidate)
            for endpoint in endpoint_ids
            for candidate in candidates
            if candidate in self._reader.list_schemas(endpoint)
        ]
        if not matches:
            raise DatabaseNotFoundError(
                f"Nenhum endpoint de banco permitido contém um banco esperado: {candidates}"
            )
        if len(matches) > 1:
            endpoints = ", ".join(f"{endpoint}:{name}" for endpoint, name in matches)
            raise AmbiguousDatabaseError(f"AMBIGUOUS_DATABASE: {endpoints}")
        endpoint, name = matches[0]
        return DatabaseLocation(DatabaseLocationStatus.FOUND, endpoint, name)
