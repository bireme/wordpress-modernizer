# ADR-005: Descoberta de banco de dados

Status: aceito. Cada endpoint da lista de permissão é consultado em busca de candidatos
explícitos. Exatamente uma correspondência é obrigatória; nenhuma correspondência ou resultados
ambíguos são erros. Substituições são recursos de primeira classe, nunca heurísticas.
