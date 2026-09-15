# Operações

## Rota de modernização WordPress

`plan` classifica cada instalação pela versão lida de `wp-includes/version.php`:

- `ANCIENT`: WordPress anterior a 4.9. A automação é bloqueada antes de qualquer mutação e o plano
  informa `manual_target_wordpress: "4.9"` e
  `reason_code: ANCIENT_WORDPRESS_REQUIRES_MANUAL_BRIDGE`;
- `LEGACY`: famílias WordPress 4.9 a 6.8, incluindo patches;
- `CURRENT`: família WordPress 6.9 ou posterior, sem checkpoint legacy pendente.

A política `wordpress-official-legacy-2026-09`, revisão 1, inclui apenas checkpoints legacy
posteriores à versão detectada: 5.3/PHP 7.4, 6.2/PHP 7.4, 6.8/PHP 8.1 a 8.4 e, por fim,
`latest` com o runtime `current`. O alvo final pode ser igual à versão já instalada;
nesse caso o download é pulado, mas as verificações e `core update-db` continuam.
Cada checkpoint atualiza o Core para a versão exata, executa
`core update-db`, verifica a versão e checksums, valida bootstrap reduzido e somente então grava o
checkpoint. Uma falha interrompe a rota e preserva a cópia de TESTE.

Se a instalação já está em 6.8 e `current` ainda não é compatível com essa versão, o plano mantém
somente `latest` como destino, mas faz o preflight com um runtime moderno compatível configurado.
Depois da atualização, a validação final ocorre sob `current`.

PHP 7.4 está EOL e é usado apenas como runtime transitório.
Os comandos WP-CLI das etapas roteadas começam com
o caminho PHP decidido no plano; nenhuma etapa chama `update-alternatives`, modifica FPM/servidor
web ou instala pacotes. `plan` pode exibir comandos administrativos como orientação, mas nunca os
executa. A orientação APT só é específica quando Debian/Ubuntu é detectado e `apt-cache policy`
confirma um candidato no repositório já configurado.

A rota segue o [guia oficial de atualização do WordPress](https://developer.wordpress.org/advanced-administration/upgrade/upgrading/)
e a [matriz oficial de compatibilidade PHP](https://make.wordpress.org/core/handbook/references/php-compatibility-and-wordpress-versions/).

- `inventory` acrescenta caminhos e configuração ao diagnóstico local. Versões WordPress/PHP/WP-CLI,
  tema ativo, plugins, URL e contagens de widgets permanecem placeholders `indisponível`.
- `diagnose` informa capacidades tipadas e integridade.
- `plan` apresenta estrutura, exclusões entre instalações cadastradas pai/filho, etapas de migração,
  pendências e rota WordPress/PHP. Lê a versão remota e inspeciona runtimes locais, mas não executa
  `snapshot_source_database`: não descobre banco de origem/destino nem URLs efetivas.
- `migrate` cria uma cópia de segurança ou substitui apenas com autorização explícita e prepara
  arquivos e banco de dados.
- `update` executa etapas independentes com pontos de controle.
- `pipeline` concatena migração e atualização e é o fluxo operacional normal.
- `resume` carrega uma execução, compara a impressão digital da instalação e só prossegue se ela
  for consistente.

`--dry-run`, no nível global ou do comando, nunca altera a instalação, seus arquivos ou seu banco.
Cada etapa declara uma capacidade revisada explicitamente:

- `READ_ONLY`: a leitura é executada e o resultado fica `VALIDATED`;
- `MUTABLE_WITHOUT_SAFE_DRY_RUN`: nenhuma porta operacional é chamada e o resultado fica
  `PLANNED`;
- `MUTABLE_WITH_NATIVE_DRY_RUN`: somente a entrada de validação separada e autorizada é chamada,
  e o resultado fica `VALIDATED`.

Uma validação também declara suas capacidades mínimas. Se elas não estiverem disponíveis, o
adapter não é chamado e a etapa permanece `PLANNED`; ausência de infraestrutura nunca transforma
uma tentativa de validação em execução implícita.

Uma execução real bem-sucedida fica `EXECUTED`. Hoje, `wp search-replace --dry-run` é a única
simulação nativa autorizada; o adapter usa bootstrap reduzido e não marca a operação pendente como
concluída. Ter uma opção chamada `--dry-run` não é suficiente para autorizar outro comando: cada
nova operação precisa ser classificada e adaptada explicitamente. A simulação ainda sonda
capacidades e grava seu manifesto externo de auditoria. Intencionalmente, não existe comando de
publicação em produção.

No fluxo de migração, `snapshot_source_database` é `READ_ONLY`: lê `wp-config.php` remotamente
para obter `DB_NAME`, `DB_HOST`, `DB_USER`, `DB_PASSWORD` e `$table_prefix`, e consulta `siteurl` no MySQL da origem com
`SELECT`. Não executa WP-CLI ou PHP remoto. Com rota pronta e capacidades exigidas disponíveis
(inclusive `DATABASE_AVAILABLE` para essa leitura), `pipeline --dry-run` resolve e registra
`source_server`, `source_path`,
`source_database`, `source_database_host`, `source_database_port`, `target_database_endpoint`, `target_database`, URL
de origem e `test_url` sem depender de `backup_existing_test` ou `copy_files`. Dump, importação,
backup e cópia de arquivos continuam apenas `PLANNED`. O dry-run valida acesso e resolução, mas
não prova espaço livre para o backup, conclusão de uma cópia grande ou sucesso futuro do dump e
da importação.

Há um limite importante na implementação atual: `DATABASE_AVAILABLE` vem da sondagem do
destino local, que precisa de `wp-config.php` válido, WP-CLI e schema acessível. Quando o
destino ainda não existe, `snapshot_source_database` pode ficar `PLANNED` no dry-run,
sem inspecionar a origem. Portanto, a independência em relação às mutações anteriores
não garante descoberta em qualquer ambiente vazio; confira o resultado de cada etapa.

O adaptador público de execução delega cópias à porta de transporte remoto. Um roteador usa
SSH/rsync para autenticação por chave e SSH/tar (Paramiko) para cópia por senha, com SFTP para ler `wp-config.php`. A
inspeção da origem reutiliza esses transportes apenas para leitura do arquivo permitido; descoberta
e transferência de bancos são delegadas ao MySQL e operações WordPress locais/de TESTE ao WP-CLI. Uma
migração de banco descobre a conexão de origem pelo arquivo remoto e usa apenas endpoints de
TESTE cadastrados para o destino. `write_test_db_config` entrega as credenciais de TESTE ao
writer local, que edita
`wp-config.php` atomicamente sem executar PHP. Não usa WP-CLI para essa escrita.

Sem porta explícita em `DB_HOST`, a conexão tenta 6612 e somente em caso de
`ENDPOINT_UNAVAILABLE` tenta 3306. `AVAILABLE` encerra a descoberta com sucesso.
`AUTHENTICATION_DENIED`, `SCHEMA_NOT_FOUND`, `CONFIGURATION_INSUFFICIENT` e `UNKNOWN`
interrompem sem fallback: trocar de serviço ocultaria uma falha que precisa ser corrigida.
Uma porta explícita válida (1–65535) é a única tentada. Falhas são sanitizadas; sockets e formatos
ambíguos são recusados.

A conexão de PRODUÇÃO é efêmera: `DB_USER` e `DB_PASSWORD` não entram em logs, exceções,
`repr()`, manifestos, state ou recovery data, nem em argumentos de subprocessos. O cliente MySQL
recebe as credenciais em `--defaults-extra-file` temporário com permissão `0600`, removido em
sucesso e erro. O snapshot de conexão guarda apenas metadados não secretos. Antes de retomar a cópia do banco,
`resume` relê o `wp-config.php`, redescobre a conexão e exige banco, host e porta iguais ao snapshot;
usuário e senha podem mudar e não são comparados nem persistidos no estado.

Com destino existente, a execução sem `--replace-existing` é recusada. Com a opção, o step
`backup_existing_test` valida novamente ambiente, estrutura, raiz permitida e ausência de symlink;
depois copia a árvore, compara uma impressão SHA-256 de conteúdo, torna o snapshot somente leitura
e publica-o com nome não colidente. Somente um registro de backup revalidado autoriza
`copy_files` a remover a árvore TESTE antiga e transferir a origem. Uma falha antes dessa validação
não remove, move nem sobrescreve o destino existente.

## Ordem real da migração

Para uma instalação, `migrate` executa:

1. `backup_existing_test`;
2. `copy_files`;
3. `snapshot_source_database`;
4. `copy_database`;
5. `write_test_db_config`;
6. `plan_multisite_domain`;
7. `correct_multisite_domain`;
8. `pending_search_replace`, quando há pendência;
9. `plan_test_https`;
10. `enforce_test_https`;
11. `plan_test_indexing`;
12. `disable_test_indexing`.

Para instalações aninhadas cadastradas, o planner ordena por profundidade e caminho:
primeiro executa as etapas 1–7 de todas, depois 8–12 de cada uma. O service cria uma
pendência de search-replace no plano normal; a execução determina o trabalho necessário.
Mesmo `migrate` e o dry-run exigem uma rota WordPress/PHP pronta no CLI atual.

No `pipeline`, toda a migração precede as etapas de atualização abaixo, por instalação.
O search-replace não é repetido na fase de atualização. O comando `update` lê a versão
local de TESTE e não executa a migração nem suas etapas Multisite/HTTPS/indexação.

## Atualização e comandos WP-CLI

A atualização começa com `preflight`, `pending_search_replace` (somente no `update`
independente) e `snapshot` de widgets. Depois executa os checkpoints
`wordpress_checkpoint_<versão>` da rota persistida. Cada um usa `core version`,
`core update --version=<versão>` se necessário, `core update-db`, nova leitura da versão,
`core verify-checksums` e leitura de `siteurl` com bootstrap reduzido.

Após os checkpoints, a ordem é:

| Etapa | Operação |
|---|---|
| `managed_plugin_refresh` | Git conforme `plugins.yaml` e a política persistida |
| `third_party_plugin_update` | `plugin update --all`, com `--exclude=<slugs-gerenciados>` quando há plugins gerenciados |
| `theme_update` | `theme update --all` |
| `core_languages` | `language core update` |
| `plugin_languages` | `language plugin update --all` |
| `theme_languages` | `language theme update --all` |
| `widget_validation` | compara snapshot e opções atuais no MySQL; restaura somente com autorização explícita |

O adapter prefixa os comandos WP-CLI com os caminhos absolutos PHP/WP-CLI selecionados,
`--path`, `--skip-plugins` e `--skip-themes`. Essas atualizações ficam `PLANNED` no dry-run.
`preflight` e `snapshot` são `READ_ONLY`; o snapshot só é lido se suas capacidades mínimas
estiverem disponíveis. Não é um backup integral do banco. As sondagens do runner após
etapas executadas validam capacidades; não existe uma etapa separada de postflight geral.

Veja [recuperação](recovery.md) para widgets e retomada, e
[Multisite](multisite.md), [HTTPS](test-https.md) e [indexação](test-indexing.md)
para as proteções da migração.

## Opções do CLI

`--config` e `--dry-run` podem preceder o subcomando. Todos os subcomandos aceitam `--json`.
`migrate`, `update` e `pipeline` aceitam também `--dry-run`, `--replace-existing` e
`--restore-widgets`; `--replace-existing` só afeta a migração, e `--restore-widgets` só
atua na validação de widgets da atualização. `resume` exige `--run-id`, aceita
`--dry-run`/`--restore-widgets` e reutiliza a autorização original de substituição,
sem aceitar `--replace-existing` novamente. Veja [códigos de saída](../README.md#códigos-de-saída).
