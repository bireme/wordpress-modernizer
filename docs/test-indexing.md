# Indexação das cópias de TESTE (P1.3)

As novas migrações configuram `blog_public=0`, a opção nativa do WordPress
“discourage search engines”. Isso **não garante bloqueio absoluto de crawlers**:
o comportamento depende dos mecanismos de busca. Não são implementados robots.txt,
headers noindex, autenticação, firewall ou alterações de infraestrutura.

Após `enforce_test_https`, o pipeline executa `plan_test_indexing` e
`disable_test_indexing`, antes de modernização/updates. A ordem de migração é:
`backup_existing_test` → `copy_files` → `snapshot_source_database` → `copy_database`
→ `write_test_db_config` → `plan_multisite_domain` → `correct_multisite_domain`
→ `pending_search_replace` (quando pendente) → `plan_test_https`
→ `enforce_test_https` → `plan_test_indexing` → `disable_test_indexing`.

Single-site: lê a opção no contexto da URL HTTPS final do site atual, atualiza apenas
se necessário e confirma `blog_public=0` com `wp option get`.
Multisite: utiliza todos os IDs/URLs finais do plano HTTPS persistido pelo P1.2,
derivado do P1.1, incluindo subdiretórios e subdomínios. Cada leitura/escrita usa
`--url=<home HTTPS final>`. A estrutura do banco e `wp site list` devem corresponder
exatamente aos blogs esperados antes, durante e depois da execução. IDs, domínios,
paths, home/siteurl, configuração HTTPS e DOMAIN_CURRENT_SITE não são alterados.
Prefixos customizados usam os ports existentes; não há SQL de escrita nesta etapa.

O RunManifest guarda em `recovery_data` o `test_indexing_plan` versionado, vinculado
ao plano HTTPS completo (destino, conexão, prefixo, configuração e topologia), com
o valor inicial por ID. O runner persiste esse plano antes da primeira escrita.
No resume, cada opção deve corresponder ao valor inicial ou a `0`; outro estado
falha fechado. Valores reconhecidos são `-1`, `0` e `1`; saída ambígua é recusada.
Blogs já em `0` são validados sem escrita, com `changed=False` se nenhum precisar
mudar. Não é necessário persistir um cursor: as opções reais indicam o progresso,
inclusive após interrupção entre escrita e checkpoint. A ordem de enumeração dos
blogs não interfere no resume.

O sucesso exige todos os blogs em `0`, topologia/URLs/configuração preservadas e
lista completa da rede validada. São persistidos `test_indexing_validated=true` e
`test_indexing_validated_count`. Erros preservam TESTE e impedem as etapas seguintes.
O destino deve ser explicitamente TEST, o endpoint deve ser TEST e o wp-config deve
corresponder à conexão de TESTE antes das operações WordPress. PRODUÇÃO não é alterada.
Dry-run apenas registra as etapas, sem bootstrap ou escrita.

A proteção começa nesta etapa: ela não cobre o intervalo anterior de cópia/correção,
nem impede que plugins ou intervenções futuras mudem a opção. Manifests antigos
continuam usando seu plano original no resume; este recurso não injeta novas etapas
em execuções antigas. Postflight geral e proteção absoluta de acesso ficam fora do P1.3.
