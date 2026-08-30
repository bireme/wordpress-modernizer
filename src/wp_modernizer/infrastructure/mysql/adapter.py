from __future__ import annotations

import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Mapping, Set

from wp_modernizer.application.ports import CommandRunner, SecretProvider
from wp_modernizer.config.models import DatabaseConfig
from wp_modernizer.domain.enums import Environment
from wp_modernizer.domain.errors import (
    AuthenticationError,
    ConfigurationError,
    InfrastructureError,
    UnsafeOperationError,
)
from wp_modernizer.domain.widgets import WidgetOption, WidgetSnapshot


class MySQLAdapter:
    """Adaptador MySQL com credenciais no ambiente e redirecionamento de fluxo, nunca shell."""

    def __init__(
        self,
        endpoints: Dict[str, DatabaseConfig],
        secrets: SecretProvider,
        runner: CommandRunner,
        mysql_bin: str = "mysql",
        mysqldump_bin: str = "mysqldump",
    ) -> None:
        self._endpoints = endpoints
        self._secrets = secrets
        self._runner = runner
        self._mysql = mysql_bin
        self._dump = mysqldump_bin

    def get_database(self, endpoint_id: str) -> DatabaseConfig:
        try:
            return self._endpoints[endpoint_id]
        except KeyError as exc:
            raise ConfigurationError(f"Endpoint MySQL desconhecido: {endpoint_id}") from exc

    def list_schemas(self, endpoint_id: str) -> Set[str]:
        result = self._query(endpoint_id, "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA")
        return set(result.splitlines())

    def dump(self, endpoint_id: str, database: str, output: Path, run_id: str) -> None:
        endpoint = self.get_database(endpoint_id)
        with self._defaults_file(endpoint) as defaults:
            result = self._runner.run(
                [
                    self._dump,
                    f"--defaults-extra-file={defaults}",
                    "--single-transaction",
                    "--quick",
                    "--default-character-set=utf8mb4",
                    database,
                ],
                stdout_path=output,
                timeout=1800,
                correlation_id=run_id,
            )
        self._ensure_success(result.return_code, result.stderr)

    def import_dump(self, endpoint_id: str, database: str, source: Path, run_id: str) -> None:
        endpoint = self.get_database(endpoint_id)
        if endpoint.environment is not Environment.TEST:
            raise UnsafeOperationError("Importações MySQL são proibidas fora de TESTE")
        with self._defaults_file(endpoint) as defaults:
            result = self._runner.run(
                [
                    self._mysql,
                    f"--defaults-extra-file={defaults}",
                    "--batch",
                    "--raw",
                    database,
                ],
                stdin_path=source,
                timeout=1800,
                correlation_id=run_id,
            )
        self._ensure_success(result.return_code, result.stderr)

    def snapshot_widgets(self, endpoint_id: str, database: str) -> WidgetSnapshot:
        tables = [
            name
            for name in self._query(endpoint_id, "SHOW TABLES", database).splitlines()
            if name.endswith("_options")
        ]
        options = []
        for table in tables:
            if not re.fullmatch(r"[A-Za-z0-9_]+", table):
                raise InfrastructureError("O banco retornou um identificador de tabela inseguro")
            # Os nomes de tabela vêm apenas de SHOW TABLES e são filtrados antes da interpolação.
            sql = (
                f"SELECT option_name,HEX(option_value),autoload FROM `{table}` "  # noqa: S608
                "WHERE option_name='sidebars_widgets' OR option_name LIKE 'widget\\_%' "
                "OR option_name IN ('template','stylesheet') ORDER BY option_name"
            )
            for line in self._query(endpoint_id, sql, database).splitlines():
                fields = line.split("\t")
                if len(fields) == 3:
                    options.append(
                        WidgetOption(table, fields[0], bytes.fromhex(fields[1]), fields[2])
                    )
        return WidgetSnapshot.from_options(options)

    def wordpress_configuration(self, endpoint_id: str, database: str) -> Mapping[str, str]:
        endpoint = self.get_database(endpoint_id)
        if endpoint.environment is not Environment.TEST:
            raise UnsafeOperationError("Configuração WordPress é proibida fora de TESTE")
        host = endpoint.host if endpoint.port == 3306 else f"{endpoint.host}:{endpoint.port}"
        return {
            # Troque o host primeiro: qualquer falha posterior já mantém o WordPress afastado
            # do endpoint de produção copiado da origem.
            "DB_HOST": host,
            "DB_NAME": database,
            "DB_USER": self._secrets.get(endpoint.username_secret),
            "DB_PASSWORD": self._secrets.get(endpoint.password_secret),
        }

    def _query(self, endpoint_id: str, sql: str, database: str = "") -> str:
        endpoint = self.get_database(endpoint_id)
        with self._defaults_file(endpoint) as defaults:
            argv = [
                self._mysql,
                f"--defaults-extra-file={defaults}",
                "--batch",
                "--raw",
                "--skip-column-names",
            ]
            if database:
                argv.append(database)
            argv.extend(["--execute", sql])
            result = self._runner.run(argv, timeout=60)
        self._ensure_success(result.return_code, result.stderr)
        return result.stdout

    @contextmanager
    def _defaults_file(self, endpoint: DatabaseConfig) -> Iterator[Path]:
        username = self._secrets.get(endpoint.username_secret)
        password = self._secrets.get(endpoint.password_secret)
        path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
                path = Path(handle.name)
                handle.write(
                    "[client]\n"
                    f"host={endpoint.host}\nport={endpoint.port}\n"
                    f"user={self._option_value(username)}\n"
                    f"password={self._option_value(password)}\n"
                )
            os.chmod(path, 0o600)
            yield path
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    @staticmethod
    def _option_value(value: str) -> str:
        escaped = (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        return f'"{escaped}"'

    @staticmethod
    def _ensure_success(return_code: int, stderr: str) -> None:
        if return_code == 0:
            return
        if "access denied" in stderr.lower():
            raise AuthenticationError("Falha na autenticação do banco de dados")
        raise InfrastructureError("Falha no comando do banco de dados; consulte o log redigido")
