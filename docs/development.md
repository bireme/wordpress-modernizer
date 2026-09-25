# Desenvolvimento e testes

Os testes unitários não precisam de WordPress, MySQL, SSH, Docker ou rede:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
pytest --cov --cov-report=term-missing
ruff check .
ruff format --check .
mypy src
python -m build
```

O teste `tests/integration/test_external_opt_in.py` é um placeholder: chama `pytest.skip`
mesmo com `WP_MODERNIZER_INTEGRATION=1`. O workflow manual apenas seleciona esse teste;
não provisiona WordPress/MySQL/SSH e ainda não comprova uma migração real.

Para implementar uma integração real em laboratório de TESTE isolado e descartável:

1. Provisione destinos descartáveis com MariaDB/WordPress/WP-CLI e uma origem SSH sintética e
   somente leitura.
2. Copie `config.example.yaml` para fora do Git; use endpoints `.invalid`/de laboratório e
   destinos de TESTE.
3. Exporte as variáveis de segredos referenciadas e `WP_MODERNIZER_INTEGRATION=1`.
4. Implemente fixtures e assertions reais no lugar do placeholder. Execute `pytest -m integration -o addopts='' --maxfail=1 -vv` localmente ou pelo workflow
   manual.
5. Inspecione os relatórios gerados em `state/<id>/runs/<run-id>` e destrua o laboratório
   descartável.

Os testes de integração são ignorados se a variável de adesão não existir. Nunca os aponte para
produção. Os workflows de PRs públicos não leem segredos e excluem o marcador `integration`.

Antes da publicação, examine a árvore de trabalho e o histórico do Git:

```bash
rg -n -i '(password|passwd|token|secret|mysql://|private.key|BEGIN.*PRIVATE)' . \
  -g '!docs/security.md' -g '!*.example' -g '!tests/**'
rg -n '([0-9]{1,3}\.){3}[0-9]{1,3}|@[A-Za-z0-9.-]+|/home/' .
```

Classifique cada ocorrência; não se limite a suprimi-la. Novos adaptadores implementam o
`Protocol` relevante, recebem `CommandRunner`/segredos por injeção e contam com testes unitários
e de contrato.

A CI executa qualidade em Python 3.10, 3.11, 3.12 e 3.13, incluindo build e cobertura
mínima de 80%. `pytest` sem `--cov` não aplica esse limite. O contrato local de serialização
usa PHP e um WP-CLI phar quando presentes e é pulado quando indisponíveis; não requer banco
nem bootstrap de WordPress. Veja [workflows](../.github/workflows/README.md).
