# Configuração

Copie `config.example.yaml` para um arquivo local ignorado pelo Git. O YAML contém a topologia e
as referências; as variáveis de ambiente contêm os valores. `EnvironmentSecretProvider` gera um
erro operacional quando falta uma referência. Um futuro adaptador de cofre precisa apenas
implementar `SecretProvider`.

Cada instalação informa um servidor de origem, um ambiente de origem (`production` ou `test`),
um caminho absoluto de origem, um destino de TESTE absoluto e IDs permitidos de endpoints de
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
`<source_path>/wp-config.php` por SSH/SFTP e extrai os literais `DB_NAME`, `DB_HOST` e
`$table_prefix`, inclusive em `--dry-run`. Ela usa a mesma autenticação e a mesma verificação de
host key do transporte, sem copiar a árvore para o destino e sem executar WP-CLI, PHP ou o
bootstrap do WordPress em PRODUÇÃO. O conteúdo integral do arquivo e seus segredos nunca são
incluídos em logs ou exceções.

Depois que endpoint e schema da origem são resolvidos, `siteurl` é lida de
`<table_prefix>options` por um `SELECT` MySQL fixo e limitado. A conta MySQL de PRODUÇÃO deve ser
somente-leitura. A descoberta não executa escrita no banco da origem.

Se `source_database_endpoint` estiver definido, ele identifica o endpoint cadastrado usado para
validar o schema e produzir o dump somente-leitura. Seu `environment` deve coincidir com
`source_environment`. Se estiver omitido, `DB_HOST` remoto deve corresponder exatamente ao
host/porta de um único endpoint cadastrado no mesmo ambiente e o `DB_NAME` deve existir nele.
Aliases DNS, sockets MySQL, formatos não suportados, ausência e múltiplas correspondências falham
sem fallback; nesses casos configure `source_database_endpoint` explicitamente.

```yaml
installations:
  example-site:
    source_database_endpoint: production-db-example
    allowed_database_endpoints: [test-db-example]
```

As credenciais do endpoint de origem são necessárias para consultas de existência, leitura de
`siteurl` e `mysqldump`,
mas nunca autorizam importação: `MySQLAdapter.import_dump` continua recusando qualquer endpoint
que não seja TESTE.

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
nunca substituem `DB_NAME`, `DB_HOST` nem `source_database_endpoint` da origem.

`organizational_domain` declara a fronteira DNS usada pela convenção de URLs de TESTE. Para
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
