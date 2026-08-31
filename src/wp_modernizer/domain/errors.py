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


class PasswordAuthenticationError(AuthenticationError):
    """A autenticação por senha foi recusada sem divulgar a credencial."""


class AuthenticationRefusedError(AuthenticationError):
    """O servidor não permite o método de autenticação solicitado."""


class HostKeyVerificationError(InfrastructureError):
    """A identidade SSH do host não pertence ao conjunto confiável."""


class RemoteHostUnreachableError(InfrastructureError):
    """Não foi possível alcançar o endpoint remoto."""


class TransferError(InfrastructureError):
    """A sessão autenticou, mas a transferência falhou."""


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
