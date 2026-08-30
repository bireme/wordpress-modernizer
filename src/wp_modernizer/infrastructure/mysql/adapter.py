from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Set

from wp_modernizer.application.ports import CommandRunner, SecretProvider
from wp_modernizer.config.models import DatabaseConfig
from wp_modernizer.domain.errors import AuthenticationError, InfrastructureError
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

    def list_schemas(self, endpoint_id: str) -> Set[str]:
        result = self._query(endpoint_id, "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA")
        return set(result.splitlines())

    def dump(self, endpoint_id: str, database: str, output: Path, run_id: str) -> None:
        endpoint = self._endpoints[endpoint_id]
        result = self._runner.run(
            [
                self._dump,
                "--host",
                endpoint.host,
                "--port",
                str(endpoint.port),
                "--user",
                self._username(endpoint),
                "--single-transaction",
                "--quick",
                "--default-character-set=utf8mb4",
                database,
            ],
            environment=self._environment(endpoint),
            stdout_path=output,
            timeout=1800,
            correlation_id=run_id,
        )
        self._ensure_success(result.return_code, result.stderr)

    def import_dump(self, endpoint_id: str, database: str, source: Path, run_id: str) -> None:
        endpoint = self._endpoints[endpoint_id]
        result = self._runner.run(
            [
                self._mysql,
                "--batch",
                "--raw",
                "--host",
                endpoint.host,
                "--port",
                str(endpoint.port),
                "--user",
                self._username(endpoint),
                database,
            ],
            environment=self._environment(endpoint),
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

    def _query(self, endpoint_id: str, sql: str, database: str = "") -> str:
        endpoint = self._endpoints[endpoint_id]
        argv = [
            self._mysql,
            "--batch",
            "--raw",
            "--skip-column-names",
            "--host",
            endpoint.host,
            "--port",
            str(endpoint.port),
            "--user",
            self._username(endpoint),
        ]
        if database:
            argv.append(database)
        argv.extend(["--execute", sql])
        result = self._runner.run(argv, environment=self._environment(endpoint), timeout=60)
        self._ensure_success(result.return_code, result.stderr)
        return result.stdout

    def _username(self, endpoint: DatabaseConfig) -> str:
        return self._secrets.get(endpoint.username_secret)

    def _environment(self, endpoint: DatabaseConfig) -> Dict[str, str]:
        return {"MYSQL_PWD": self._secrets.get(endpoint.password_secret)}

    @staticmethod
    def _ensure_success(return_code: int, stderr: str) -> None:
        if return_code == 0:
            return
        if "access denied" in stderr.lower():
            raise AuthenticationError("Falha na autenticação do banco de dados")
        raise InfrastructureError(f"Falha no comando do banco de dados: {stderr}")
