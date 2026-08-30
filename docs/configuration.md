# Configuração

Copie `config.example.yaml` para um arquivo local ignorado pelo Git. O YAML contém a topologia e
as referências; as variáveis de ambiente contêm os valores. `EnvironmentSecretProvider` gera um
erro operacional quando falta uma referência. Um futuro adaptador de cofre precisa apenas
implementar `SecretProvider`.

Cada instalação informa um servidor de origem, um ambiente de origem (`production` ou `test`),
um caminho absoluto de origem, um destino de TESTE absoluto e IDs permitidos de endpoints de
banco de dados de teste. Apelidos e substituições exatas de bancos são explícitos. Por padrão,
não é permitida a criação de bancos inexistentes.
