class ModernizerError(Exception):
    """Erro operacional base que pode ser exibido com segurança pela CLI."""


class ConfigurationError(ModernizerError):
    pass


class InfrastructureError(ModernizerError):
    pass


class CommandTimeoutError(InfrastructureError):
    pass


class AuthenticationError(InfrastructureError):
    pass


class DiagnosticError(ModernizerError):
    pass


class WordPressUnavailableError(DiagnosticError):
    pass


class DatabaseNotFoundError(DiagnosticError):
    pass


class AmbiguousDatabaseError(DiagnosticError):
    pass


class UnsafeOperationError(ModernizerError):
    pass


class ResumeConsistencyError(ModernizerError):
    pass
