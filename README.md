# wp-modernizer

O `wp-modernizer` prepara e atualiza cópias de instalações WordPress em um ambiente de
**teste**, usando um fluxo de falha com preservação:

```text
origem -> cópia de teste -> atualização -> validação
                                      -> falha -> parar e preservar -> correção manual -> retomar
```

**Este projeto nunca implanta em produção.** Seu modelo de domínio rejeita um destino de
produção e, deliberadamente, não expõe comandos para promover, enviar, sincronizar com produção
ou implantar.

O pacote usa uma estrutura `src`, adaptadores de infraestrutura com inversão de dependência,
estado de execução tipado, planos de migração explícitos, níveis de capacidade, instantâneos de
widgets obtidos diretamente do banco de dados e redação centralizada. WordPress, MySQL, SSH e
WP-CLI não são necessários para os testes unitários.

## Início rápido

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
cp config.example.yaml config.yaml
wp-modernizer --config config.yaml inventory example-site
wp-modernizer --config config.yaml plan example-site --json
wp-modernizer --config config.yaml pipeline example-site --dry-run
pytest
ruff check . && ruff format --check .
mypy src
```

`config.yaml` é local, específico do servidor e pode conter referências sensíveis, por isso não é
versionado. A lista pública e compartilhada de plugins gerenciados fica no `plugins.yaml` que já
acompanha a aplicação e é versionado no repositório. Seu caminho é fixo e não precisa — nem pode —
ser informado pelo usuário, portanto o fluxo acima não exige nenhum passo adicional.

O domínio é inferido individualmente de `source_path`, relativamente à raiz de
`allowed_app_roots`: `/home/apps/example.org/wp-example/htdocs` sob `/home/apps` identifica
`example.org`. Instalações diferentes podem pertencer a domínios diferentes. `destination_path`
é opcional: omitido, usa o mesmo caminho de `source_path` no servidor operacional de TESTE.
Um caminho explícito continua permitido como exceção.

Cadastre em `databases:` somente endpoints de TESTE. Banco, host, usuário, senha e prefixo da
origem são descobertos por parsing literal de `wp-config.php` remoto, sem executar PHP ou WP-CLI
em PRODUÇÃO. Sem porta explícita, tenta 6612 e depois 3306 apenas por falha de conectividade.
Credenciais da origem permanecem efêmeras e fora do estado e dos logs.

Os comandos que alteram estado são `migrate`, `update`, `pipeline` e `resume`. Primeiro,
`migrate --replace-existing` cria uma cópia de segurança da cópia de teste existente; em seguida,
exige que todas as proteções de caminhos destrutivos sejam aprovadas. `diagnose`, `inventory` e
`plan` são somente leitura. Todos os comandos aceitam `--json`; `--dry-run` é uma opção global e
garante que o alvo não seja alterado. Etapas somente leitura ficam `VALIDATED`, etapas mutáveis sem
simulação segura ficam `PLANNED`, e apenas dry-runs nativos revisados explicitamente podem validar
uma etapa mutável. Execuções reais bem-sucedidas ficam `EXECUTED`.

Consulte [operações](docs/operations.md), [configuração](docs/configuration.md),
[arquitetura](docs/architecture.md) e [recuperação](docs/recovery.md).
