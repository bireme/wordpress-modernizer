# Configuração

Copie `config.example.yaml` para um arquivo local ignorado pelo Git. O YAML contém a topologia e
as referências; as variáveis de ambiente contêm os valores. `EnvironmentSecretProvider` gera um
erro operacional quando falta uma referência. Um futuro adaptador de cofre precisa apenas
implementar `SecretProvider`.

Cada instalação informa um servidor de origem, um ambiente de origem (`production` ou `test`),
um caminho absoluto de origem, um destino de TESTE absoluto e IDs permitidos de endpoints de
banco de dados de teste. Apelidos e substituições exatas de bancos são explícitos. Por padrão,
o modernizer opera somente sobre schemas previamente provisionados pela infraestrutura.

## Bancos de dados

O nome da origem é sempre descoberto de `DB_NAME`. Quando ele segue exatamente
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
