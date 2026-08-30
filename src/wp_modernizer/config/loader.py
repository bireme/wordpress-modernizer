from pathlib import Path

import yaml
from pydantic import ValidationError

from wp_modernizer.domain.errors import ConfigurationError

from .models import ApplicationConfig


def load_config(path: Path) -> ApplicationConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"Não foi possível carregar a configuração: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError("A raiz da configuração deve ser um mapeamento")
    try:
        return ApplicationConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigurationError(f"Configuração inválida: {exc}") from exc
