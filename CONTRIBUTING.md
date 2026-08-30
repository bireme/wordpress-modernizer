# Como contribuir

Use Python 3.10 ou mais recente. Instale `.[dev]`, adicione testes tipados e execute `pytest`,
`ruff check .`, `ruff format --check .` e `mypy src`. Os testes de integração exigem o marcador
`integration`, devem ser ignorados quando não houver configuração explícita do ambiente e nunca
devem acessar sistemas reais em pull requests públicos. Novas integrações de infraestrutura
devem implementar um `Protocol` da camada de aplicação.
