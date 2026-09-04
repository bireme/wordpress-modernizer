from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from wp_modernizer.domain.errors import ConfigurationError

from .models import ApplicationConfig, PluginsConfig, ServerEnvironmentConfig


def _fixed_plugins_config_path() -> Path:
    package_directory = Path(__file__).resolve().parents[1]
    repository_root = package_directory.parents[1]
    if (repository_root / "pyproject.toml").is_file():
        return repository_root / "plugins.yaml"
    return package_directory / "plugins.yaml"


PLUGINS_CONFIG_PATH = _fixed_plugins_config_path()


def _load_yaml_mapping(path: Path, document_name: str) -> dict[str, Any]:
    try:
        contents = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(f"Não foi possível ler {document_name} em {path}: {exc}") from exc
    try:
        raw = yaml.safe_load(contents)
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"YAML inválido em {document_name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError(f"A raiz de {document_name} deve ser um mapeamento")
    return raw


def load_config(path: Path) -> ApplicationConfig:
    raw_config = _load_yaml_mapping(path, "config.yaml")
    try:
        server_config = ServerEnvironmentConfig.model_validate(raw_config)
    except ValidationError as exc:
        raise ConfigurationError(f"Configuração inválida em config.yaml: {exc}") from exc

    raw_plugins = _load_yaml_mapping(PLUGINS_CONFIG_PATH, "plugins.yaml")
    try:
        plugins_config = PluginsConfig.model_validate(raw_plugins)
    except ValidationError as exc:
        raise ConfigurationError(f"Configuração inválida em plugins.yaml: {exc}") from exc

    return ApplicationConfig(
        **server_config.model_dump(), managed_plugins=plugins_config.managed_plugins
    )
