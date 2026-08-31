import re
from dataclasses import dataclass
from typing import Iterable, Protocol, Sequence, Set, Tuple

from .enums import DatabaseLocationStatus, Environment
from .errors import AmbiguousDatabaseError, DatabaseNotFoundError, UnsafeOperationError


class DatabaseNamingStrategy(Protocol):
    def candidates(self, source_name: str, aliases: Sequence[str]) -> Tuple[str, ...]: ...


class ProductionTestDatabaseNamingStrategy:
    """Aplica somente a convenção exata wp_<name>_prod -> wp_<name>_tst."""

    _production_pattern = re.compile(r"^wp_(?P<name>[A-Za-z0-9_]+)_prod$")

    def candidates(self, source_name: str, aliases: Sequence[str]) -> Tuple[str, ...]:
        match = self._production_pattern.fullmatch(source_name)
        conventional = (f"wp_{match.group('name')}_tst",) if match else ()
        # Aliases são nomes completos e exatos, nunca aproximações do nome de produção.
        return tuple(dict.fromkeys((*conventional, *(alias for alias in aliases if alias))))


class DatabaseEndpoint(Protocol):
    environment: Environment


class SchemaReader(Protocol):
    def get_database(self, endpoint_id: str) -> DatabaseEndpoint: ...

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
        *,
        override: str | None = None,
    ) -> DatabaseLocation:
        endpoints = tuple(endpoint_ids)
        for endpoint_id in endpoints:
            if self._reader.get_database(endpoint_id).environment is not Environment.TEST:
                raise UnsafeOperationError(
                    f"O endpoint {endpoint_id} não é de TESTE e não pode ser destino"
                )

        candidates = (override,) if override else self._naming.candidates(source_name, aliases)
        if not candidates:
            raise DatabaseNotFoundError(
                f"O banco de produção {source_name!r} não segue wp_<name>_prod e não possui "
                "database_override nem alias exato; configure o destino provisionado pela "
                "infraestrutura"
            )

        schemas = {endpoint_id: self._reader.list_schemas(endpoint_id) for endpoint_id in endpoints}
        matches = sorted(
            {
                (endpoint_id, candidate)
                for endpoint_id, available in schemas.items()
                for candidate in candidates
                if candidate in available
            }
        )
        if not matches:
            if override:
                raise DatabaseNotFoundError(
                    f"O database_override {override!r} não existe nos endpoints de TESTE "
                    "autorizados; a infraestrutura precisa provisionar ou corrigir a configuração"
                )
            raise DatabaseNotFoundError(
                f"Nenhum candidato exato {candidates!r} existe nos endpoints de TESTE "
                "autorizados; a infraestrutura precisa provisionar ou configurar o banco"
            )
        if len(matches) > 1:
            locations = ", ".join(f"{endpoint}:{name}" for endpoint, name in matches)
            raise AmbiguousDatabaseError(f"AMBIGUOUS_DATABASE: {locations}")
        endpoint, name = matches[0]
        return DatabaseLocation(DatabaseLocationStatus.FOUND, endpoint, name)
