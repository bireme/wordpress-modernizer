# ADR-005: Descoberta de banco de dados

Status: aceito. A origem é lida diretamente do WordPress em `source_server/source_path` por uma
porta remota somente-leitura. Seu endpoint é `source_database_endpoint` ou uma correspondência
exata entre `DB_HOST` e os endpoints cadastrados no mesmo ambiente; o schema `DB_NAME` precisa
existir. Essa resolução nunca consulta nem amplia `allowed_database_endpoints`.

O destino é procurado exclusivamente em `allowed_database_endpoints`, que aceita apenas endpoints
de TESTE. Exatamente uma correspondência é obrigatória em cada lado; ausência e ambiguidade são
erros. Overrides afetam somente o destino. Não há fallback por similaridade.
