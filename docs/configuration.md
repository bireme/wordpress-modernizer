# Configuração

Copie `config.example.yaml` para um arquivo local ignorado pelo Git. Esse `config.yaml` contém a
topologia e as referências específicas do servidor/ambiente; as variáveis de ambiente contêm os
valores secretos. `EnvironmentSecretProvider` gera um erro operacional quando falta uma
referência. Um futuro adaptador de cofre precisa apenas implementar `SecretProvider`.

## Plugins gerenciados

A lista pública, padronizada e compartilhada de plugins gerenciados fica separadamente em
`plugins.yaml`. Esse arquivo é versionado junto com a aplicação e é a única fonte da lista. A
aplicação sempre o carrega de sua localização fixa na distribuição; não existe opção de YAML,
variável de ambiente ou argumento de linha de comando para escolher outro caminho. Assim, basta
continuar passando somente a configuração local do servidor:

```bash
wp-modernizer --config config.yaml inventory example-site
```

Cada item de `managed_plugins` em `plugins.yaml` é validado antes da execução, inclusive slug,
repositório, branch, estratégia e política para alterações locais. Ausência, falha de leitura,
YAML malformado ou estrutura inválida interrompem o carregamento com um erro de configuração.

Cada instalação informa um servidor de origem, um ambiente de origem (`production` ou `test`),
um caminho absoluto de origem, um destino de TESTE opcional e IDs permitidos de endpoints de
banco de dados de teste. `allowed_database_endpoints` é validado como uma allowlist exclusivamente
de destinos TESTE; incluir PRODUÇÃO nesse campo é erro de configuração. Apelidos e substituições
exatas de bancos são explícitos. Por padrão, o modernizer opera somente sobre schemas previamente
provisionados pela infraestrutura.

## Diretório de estado

`state_directory` sustenta o mecanismo de falha com preservação e o comando `resume`. Antes de
uma execução real de `migrate`, `update`, `pipeline` ou `resume`, o modernizer cria o diretório
quando ele ainda não existe e comprova, com um arquivo temporário, que consegue gravar e ler o
estado. A operação mutável é recusada antes das sondagens e das alterações no destino se essa
comprovação falhar.

Em container, **`state_directory` deve estar em um volume persistente**. Configure-o para um
caminho montado que sobreviva à recriação, reinicialização ou substituição do container. Um
caminho gravável dentro da camada efêmera do container pode passar no preflight, mas não oferece
a durabilidade necessária para investigar uma falha e executar `resume`; o modernizer não tenta
inferir genericamente a persistência do filesystem.

## Bancos de dados

A origem e o destino têm resoluções independentes. A porta de inspeção remota lê somente
`<source_path>/wp-config.php` por SSH/SFTP e extrai os literais `DB_NAME`, `DB_HOST`, `DB_USER`, `DB_PASSWORD` e
`$table_prefix`, inclusive em `--dry-run`. Ela usa a mesma autenticação e a mesma verificação de
host key do transporte, sem copiar a árvore para o destino e sem executar WP-CLI, PHP ou o
bootstrap do WordPress em PRODUÇÃO. O conteúdo integral do arquivo e seus segredos nunca são
incluídos em logs ou exceções.

Depois que endpoint e schema da origem são resolvidos, `siteurl` é lida de
`<table_prefix>options` por um `SELECT` MySQL fixo e limitado. A conta MySQL de PRODUÇÃO deve ser
somente-leitura. A descoberta não executa escrita no banco da origem.

`databases:` aceita apenas endpoints configurados/controlados de TESTE (`DatabaseConfig`).
Bancos de PRODUÇÃO não precisam e não podem ser cadastrados ali. A descoberta cria uma
`SourceDatabaseConnection` diretamente dos literais remotos, sem enumerar endpoints cadastrados
ou consultar endpoints de TESTE para localizar a origem. `allowed_database_endpoints` continua
exclusivamente uma allowlist destrutiva de TESTE e nunca é ampliada pela descoberta.

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

O nome da origem é sempre descoberto do `DB_NAME` remoto. Quando ele segue exatamente
`wp_<name>_prod`, o candidato automático é `wp_<name>_tst`. A busca usa igualdade exata e ocorre
somente nos schemas dos endpoints autorizados cujo `environment` é `test`; não existe busca por
similaridade.

`database_override` dentro da instalação tem precedência absoluta e deve conter o nome completo
de um schema já provisionado. `database_aliases` contém nomes completos alternativos, também
comparados de forma exata; eles são considerados junto ao candidato convencional e, portanto,
dois nomes existentes causam erro de ambiguidade. O mapa global `database_overrides` continua
aceito por compatibilidade, com precedência menor que o override da instalação. Ausência ou
ambiguidade sempre interrompe a operação para provisionamento/correção pela infraestrutura.
Não existe opção pública nem comando para criação de banco. Configurações antigas contendo
`allow_create`, com qualquer valor, são recusadas com uma orientação explícita de migração:
remover a chave e solicitar o provisionamento do schema de TESTE.

`database_override` e o mapa legado `database_overrides` alteram apenas o nome de destino. Eles
nunca substituem os valores descobertos na origem.

O domínio é inferido individualmente de `source_path`, relativamente à raiz de
`allowed_app_roots`: `/home/apps/example.org/wp-example/htdocs` sob `/home/apps` identifica
`example.org`. Instalações diferentes podem pertencer a domínios diferentes. `destination_path`
é opcional: omitido, usa o mesmo caminho de `source_path` no servidor operacional de TESTE.
Um caminho explícito continua permitido como exceção.

Na migração de configurações antigas, remova `organizational_domain` e
`source_database_endpoint`: ambos foram removidos do YAML e são rejeitados.

A convenção de URLs usa o domínio inferido. Para
`bireme.org`, a URL de produção `https://boletin.bireme.org` resulta em
`https://boletin.teste.bireme.org`, enquanto `https://bireme.org` resulta em
`https://teste.bireme.org`. O path da URL é preservado. Uma instalação excepcional pode definir
`test_url`; o valor explícito prevalece, mas não pode reutilizar o hostname de produção.

## Transporte SSH

Cada servidor escolhe o transporte explicitamente em `authentication`. O cenário principal de
implantação usa `password`, com `username_secret` e `password_secret` apontando para entradas do
`SecretProvider`:

```yaml
servers:
  source-example:
    host: source.example.org
    port: 22
    environment: production
    username_secret: PROD_EXAMPLE_USERNAME
    authentication: password
    password_secret: PROD_EXAMPLE_PASSWORD
    host_key_policy: strict
```

O valor da senha não pertence ao YAML. O adapter SFTP obtém usuário e senha somente no momento da
conexão e os fornece à API do Paramiko, sem shell ou subprocesso. `authentication: key` continua
disponível com `private_key` e usa OpenSSH/rsync; os dois mecanismos são adapters separados.

Com `host_key_policy: strict`, o transporte carrega o `~/.ssh/known_hosts` da conta que executa a
aplicação e rejeita chaves desconhecidas ou alteradas. `known_hosts_file` pode indicar um arquivo
OpenSSH adicional, por exemplo `/etc/wp-modernizer/known_hosts`. O arquivo deve ser provisionado
antes do preflight por um canal confiável. Não use `accept-new` em produção.
