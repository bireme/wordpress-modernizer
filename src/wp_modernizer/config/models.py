from pathlib import Path
from typing import Dict, List, Literal, Mapping, Optional

from pydantic import AnyHttpUrl, BaseModel, Field, ValidationInfo, field_validator, model_validator

from wp_modernizer.domain.enums import Environment
from wp_modernizer.domain.test_url import OrganizationalTestUrlPolicy


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
    source_server: str
    source_environment: Environment
    source_path: Path
    destination_path: Path
    destination_environment: Environment = Environment.TEST
    source_database_endpoint: Optional[str] = None
    test_url: Optional[AnyHttpUrl] = None
    allowed_database_endpoints: List[str]
    database_aliases: List[str] = Field(default_factory=list)
    database_override: Optional[str] = None
    core_checkpoints: List[str] = Field(default_factory=list)

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


class ApplicationConfig(BaseModel):
    state_directory: Path = Path("state")
    organizational_domain: str = "bireme.org"
    allowed_app_roots: List[Path]
    servers: Dict[str, ServerConfig]
    databases: Dict[str, DatabaseConfig]
    installations: Dict[str, InstallationConfig]
    database_overrides: Dict[str, str] = Field(default_factory=dict)
    managed_plugins: List[ManagedPluginConfig] = Field(default_factory=list)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    @field_validator("organizational_domain")
    @classmethod
    def organizational_domain_is_valid(cls, value: str) -> str:
        return OrganizationalTestUrlPolicy(value).organizational_domain

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
            if item.source_database_endpoint is not None:
                source_endpoint = databases.get(item.source_database_endpoint)
                if source_endpoint is None:
                    raise ValueError(
                        f"a instalação {key} referencia um banco de origem desconhecido"
                    )
                if source_endpoint.environment is not item.source_environment:
                    raise ValueError(
                        f"o banco de origem de {key} não pertence a source_environment"
                    )
        return value
