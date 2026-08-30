import os

from wp_modernizer.domain.errors import ConfigurationError


class EnvironmentSecretProvider:
    def get(self, reference: str) -> str:
        value = os.environ.get(reference)
        if value is None:
            raise ConfigurationError(
                f"A variável de ambiente de segredo obrigatória não está definida: {reference}"
            )
        return value
