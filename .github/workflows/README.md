# Workflows

| Arquivo | Disparo | Verificações |
|---|---|---|
| `ci.yml` | push e pull request | Python 3.10–3.13: Ruff lint/formatação, mypy, pytest com cobertura e build; job separado Python 3.12 com `pip-audit` |
| `codeql.yml` | push em `master`, pull request e segunda-feira às 04:17 UTC | análise Python com CodeQL |
| `integration.yml` | `workflow_dispatch` | Python 3.12, `pytest -m integration -o addopts='' -vv` |

A CI padrão exclui `integration` por `pyproject.toml`, sem credenciais de infraestrutura.
Ela acessa a rede para instalar dependências e executar auditoria; não provisiona WordPress.
O contrato de serialização PHP/WP-CLI é opcional e pode ser pulado.

O workflow manual exige a variável de repositório `ENABLE_DISPOSABLE_INTEGRATION=true`,
usa o environment `disposable-test-lab` e define `WP_MODERNIZER_INTEGRATION=1`.
**O único teste externo atual é um placeholder que sempre é pulado**, mesmo nesse fluxo.
Não há provisionamento de laboratório ou execução real dos adapters neste workflow.
Fixtures e testes de laboratório ainda precisam ser implementados; nunca forneça
credenciais de PRODUÇÃO. Veja [desenvolvimento](../../docs/development.md).

`ci.yml` e `integration.yml` têm `contents: read`; CodeQL acrescenta
`security-events: write` para os resultados da análise.
