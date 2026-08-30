# ADR-004: Gerenciamento de segredos

Status: aceito. A configuração contém apenas referências. As variáveis de ambiente são o primeiro
provedor; provedores futuros implementam `SecretProvider`. Os valores são ocultados de forma
centralizada e nunca são serializados.
