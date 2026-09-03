# ADR-005: Descoberta de banco de dados

Status: aceito. A origem é inspecionada por uma porta remota somente-leitura que lê
`<source_path>/wp-config.php` via SSH/SFTP e extrai, com parser literal restritivo, somente
`DB_NAME`, `DB_HOST` e `$table_prefix`. Não há WP-CLI, PHP CLI, bootstrap, `eval` ou alteração no
servidor WordPress de PRODUÇÃO. Seu endpoint é `source_database_endpoint` ou uma correspondência
exata entre `DB_HOST` e os endpoints cadastrados no mesmo ambiente; o schema `DB_NAME` precisa
existir. `siteurl` é obtida depois por um `SELECT` limitado em `<table_prefix>options`. Essa
resolução nunca consulta nem amplia `allowed_database_endpoints`, e nenhum conteúdo integral ou
segredo de `wp-config.php` é registrado.

O destino é procurado exclusivamente em `allowed_database_endpoints`, que aceita apenas endpoints
de TESTE. Exatamente uma correspondência é obrigatória em cada lado; ausência e ambiguidade são
erros. Overrides afetam somente o destino. Não há fallback por similaridade.
