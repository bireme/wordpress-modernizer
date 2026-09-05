# ADR-005: Descoberta de banco de dados

Status: aceito.

A origem é inspecionada por uma porta remota somente-leitura que lê
`<source_path>/wp-config.php` via SSH/SFTP e extrai por parsing literal restritivo
`DB_NAME`, `DB_HOST`, `DB_USER`, `DB_PASSWORD` e `$table_prefix`. Valores dinâmicos ou ambíguos
são recusados. Nenhum PHP, WP-CLI, eval ou bootstrap é executado em PRODUÇÃO.
`siteurl` é obtida por SELECT limitado em `<table_prefix>options`.

`SourceDatabaseConfiguration` e `SourceDatabaseConnection` transportam os valores efêmeros.
`DatabaseConfig` representa exclusivamente endpoints controlados de TESTE em `databases:`.
A origem nunca é convertida nesse modelo, não usa `endpoint_ids()` e não depende de endpoints
cadastrados. A antiga configuração `source_database_endpoint` foi removida.

Sem porta explícita em `DB_HOST`, a conexão tenta 6612 e somente em caso de
`ENDPOINT_UNAVAILABLE` tenta 3306. `AVAILABLE` encerra a descoberta com sucesso.
`AUTHENTICATION_DENIED`, `SCHEMA_NOT_FOUND`, `CONFIGURATION_INSUFFICIENT` e `UNKNOWN`
interrompem sem fallback: trocar de serviço ocultaria uma falha que precisa ser corrigida.
Uma porta explícita válida (1–65535) é a única tentada. Falhas são sanitizadas; sockets e formatos
ambíguos são recusados.

A conexão de PRODUÇÃO é efêmera: `DB_USER` e `DB_PASSWORD` não entram em logs, exceções,
`repr()`, manifestos, state ou recovery data, nem em argumentos de subprocessos. O cliente MySQL
recebe as credenciais em `--defaults-extra-file` temporário com permissão `0600`, removido em
sucesso e erro. O estado guarda apenas metadados não secretos. Antes de retomar a cópia do banco,
`resume` relê o `wp-config.php`, redescobre a conexão e exige banco, host e porta iguais ao snapshot;
usuário e senha podem mudar e não são comparados nem persistidos no estado.

O destino é procurado exclusivamente em `allowed_database_endpoints`, a allowlist destrutiva
de TESTE. Exatamente uma correspondência é obrigatória; ausência e ambiguidade são erros.
Overrides afetam somente o destino. Não há fallback por similaridade nem criação de schemas.

O domínio é inferido individualmente de `source_path`, relativamente à raiz de
`allowed_app_roots`: `/home/apps/example.org/wp-example/htdocs` sob `/home/apps` identifica
`example.org`. Instalações diferentes podem pertencer a domínios diferentes. `destination_path`
é opcional: omitido, usa o mesmo caminho de `source_path` no servidor operacional de TESTE.
Um caminho explícito continua permitido como exceção.
