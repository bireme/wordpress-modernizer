from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from wp_modernizer.domain.enums import Environment
from wp_modernizer.domain.path_parser import InstallationPathParser


class ServerConfig(BaseModel):
    host: str
    port: int = Field(22, ge=1, le=65535)
    environment: Environment
    username_secret: str
    authentication: Literal["key", "password"] = "key"
    private_key: Optional[Path] = None
    password_secret: Optional[str] = None
    host_key_policy: Literal["strict", "accept-new"] = "strict"
    known_hosts_file: Optional[Path] = None

    @model_validator(mode="after")
    def password_required_for_compatibility(self) -> "ServerConfig":
        if self.authentication == "password" and not self.password_secret:
            raise ValueError("a autenticação por senha requer password_secret")
        if self.authentication == "password" and self.private_key is not None:
            raise ValueError("a autenticação por senha não utiliza private_key")
        return self


class DatabaseConfig(BaseModel):
    host: str
    port: int = Field(3306, ge=1, le=65535)
    environment: Environment = Environment.TEST
    username_secret: str
    password_secret: str

    @field_validator("environment")
    @classmethod
    def endpoint_is_test_only(cls, value: Environment) -> Environment:
        if value is not Environment.TEST:
            raise ValueError("databases aceita somente endpoints controlados de TESTE")
        return value

    @model_validator(mode="before")
    @classmethod
    def reject_removed_allow_create(cls, value: object) -> object:
        if isinstance(value, Mapping) and "allow_create" in value:
            raise ValueError(
                "allow_create foi removido: o wp-modernizer nunca cria bancos; remova a chave "
                "e solicite à infraestrutura o provisionamento prévio do schema de TESTE"
            )
        return value


class InstallationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_server: str
    source_environment: Environment
    source_path: Path
    destination_path: Optional[Path] = None
    destination_environment: Environment = Environment.TEST
    test_url: Optional[AnyHttpUrl] = None
    allowed_database_endpoints: List[str]
    database_aliases: List[str] = Field(default_factory=list)
    database_override: Optional[str] = None
    core_checkpoints: List[str] = Field(default_factory=list)

    @property
    def effective_destination_path(self) -> Path:
        """Return the single path consumed by inventory, planning, and runtime."""
        return effective_destination_path(self)

    @field_validator("destination_environment")
    @classmethod
    def destination_is_test(cls, value: Environment) -> Environment:
        if value is not Environment.TEST:
            raise ValueError("destination_environment deve ser test")
        return value

    @field_validator("test_url")
    @classmethod
    def test_url_is_https(cls, value: Optional[AnyHttpUrl]) -> Optional[AnyHttpUrl]:
        if value is not None and value.scheme != "https":
            raise ValueError("test_url deve usar HTTPS")
        return value

    @field_validator("database_aliases")
    @classmethod
    def database_aliases_are_exact_names(cls, value: List[str]) -> List[str]:
        normalized = [name.strip() for name in value]
        if any(not name for name in normalized):
            raise ValueError("database_aliases não pode conter nomes vazios")
        if len(normalized) != len(set(normalized)):
            raise ValueError("database_aliases não pode conter nomes duplicados")
        return normalized

    @field_validator("database_override")
    @classmethod
    def database_override_is_not_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("database_override não pode ser vazio")
        return normalized


class ManagedPluginConfig(BaseModel):
    slug: str
    repository: str
    branch: str = "main"
    strategy: Literal["replace_from_git"] = "replace_from_git"
    dirty_policy: Literal["abort", "skip"] = "abort"

    @field_validator("slug")
    @classmethod
    def slug_is_a_safe_directory_name(cls, value: str) -> str:
        normalized = value.strip()
        safe_characters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
        if (
            not normalized
            or normalized in {".", ".."}
            or any(character not in safe_characters for character in normalized)
        ):
            raise ValueError("slug de plugin deve ser um nome de diretório simples e seguro")
        return normalized

    @field_validator("repository", "branch")
    @classmethod
    def git_values_are_not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("repository e branch não podem ser vazios")
        return normalized


class ObservabilityConfig(BaseModel):
    json_stdout: bool = True
    log_file: Optional[Path] = None
    otel_enabled: bool = False


class ServerEnvironmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state_directory: Path = Path("state")
    allowed_app_roots: List[Path]
    servers: Dict[str, ServerConfig]
    databases: Dict[str, DatabaseConfig]
    installations: Dict[str, InstallationConfig]
    database_overrides: Dict[str, str] = Field(default_factory=dict)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    @field_validator("database_overrides")
    @classmethod
    def legacy_database_overrides_are_not_blank(cls, value: Dict[str, str]) -> Dict[str, str]:
        if any(
            not installation.strip() or not database.strip()
            for installation, database in value.items()
        ):
            raise ValueError("database_overrides não pode conter chaves ou nomes vazios")
        return {installation.strip(): database.strip() for installation, database in value.items()}

    @field_validator("allowed_app_roots")
    @classmethod
    def roots_are_absolute(cls, value: List[Path]) -> List[Path]:
        if not value or any(not item.is_absolute() for item in value):
            raise ValueError("allowed_app_roots deve conter caminhos absolutos")
        return value

    @field_validator("installations")
    @classmethod
    def validate_installation_references(
        cls, value: Dict[str, InstallationConfig], info: ValidationInfo
    ) -> Dict[str, InstallationConfig]:
        raw_servers = info.data.get("servers", {})
        raw_databases = info.data.get("databases", {})
        servers = raw_servers if isinstance(raw_servers, dict) else {}
        databases = raw_databases if isinstance(raw_databases, dict) else {}
        for key, item in value.items():
            if item.source_server not in servers:
                raise ValueError(f"a instalação {key} referencia um servidor desconhecido")
            unknown = set(item.allowed_database_endpoints) - set(databases)
            if unknown:
                raise ValueError(
                    f"a instalação {key} referencia bancos desconhecidos: {sorted(unknown)}"
                )
            non_test = [
                endpoint_id
                for endpoint_id in item.allowed_database_endpoints
                if databases[endpoint_id].environment is not Environment.TEST
            ]
            if non_test:
                raise ValueError(
                    "allowed_database_endpoints aceita somente destinos de TESTE; "
                    f"endpoints inválidos em {key}: {sorted(non_test)}"
                )
        return value

    @model_validator(mode="after")
    def installation_paths_are_safe_and_unambiguous(self) -> "ServerEnvironmentConfig":
        parser = InstallationPathParser(self.allowed_app_roots)
        for installation_id, item in self.installations.items():
            parser.parse(str(item.source_path), installation_id, item.source_environment)
            parser.parse(
                str(item.effective_destination_path),
                installation_id,
                item.destination_environment,
            )
        return self


class PluginsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    managed_plugins: List[ManagedPluginConfig]


class ApplicationConfig(ServerEnvironmentConfig):
    managed_plugins: List[ManagedPluginConfig] = Field(default_factory=list)


def effective_destination_path(installation: Any) -> Path:
    """Resolve the destination once for configuration and structural port implementations."""
    return Path(getattr(installation, "destination_path", None) or installation.source_path)
