# Como contribuir

Use Python 3.10 ou mais recente. Instale `.[dev]`, adicione testes tipados e execute
`pytest --cov --cov-report=term-missing`, `ruff check .`, `ruff format --check .`,
`mypy src` e `python -m build`. Os testes de integração exigem o marcador
`integration`, devem ser ignorados quando não houver configuração explícita do ambiente e nunca
devem acessar sistemas reais em pull requests públicos. Novas integrações de infraestrutura
devem implementar um `Protocol` da camada de aplicação.

A suíte externa atual é um placeholder sempre pulado; não representa cobertura real de
WordPress/MySQL/SSH. Consulte [desenvolvimento](docs/development.md) para o contrato local
opcional e a implementação das fixtures de laboratório.

Em mudanças documentais, confronte afirmações com implementação, testes, modelos de
configuração e workflows. Preserve decisões históricas dos ADRs e verifique links relativos.
