# Operações

- `inventory` coleta campos independentes e marca valores indisponíveis em vez de interromper.
- `diagnose` informa capacidades tipadas e integridade.
- `plan` apresenta exclusões de cópia entre pai/filho, estado da resolução do banco de dados,
  etapas, pontos de controle e trabalho pendente que considera a serialização, sem alterar estado.
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
para obter `DB_NAME`, `DB_HOST` e `$table_prefix`, e consulta `siteurl` no MySQL da origem com
`SELECT`. Não executa WP-CLI ou PHP remoto. Assim, `pipeline --dry-run` resolve e registra
`source_server`, `source_path`,
`source_database`, `source_database_endpoint`, `target_database_endpoint`, `target_database`, URL
de origem e `test_url` sem depender de `backup_existing_test` ou `copy_files`. Dump, importação,
backup e cópia de arquivos continuam apenas `PLANNED`. O dry-run valida acesso e resolução, mas
não prova espaço livre para o backup, conclusão de uma cópia grande ou sucesso futuro do dump e
da importação.

O adaptador público de execução delega cópias à porta de transporte remoto. Um roteador usa
SSH/rsync para autenticação por chave e SSH/SFTP (Paramiko) para autenticação por senha. A
inspeção da origem reutiliza esses transportes apenas para leitura do arquivo permitido; descoberta
e transferência de bancos são delegadas ao MySQL e operações WordPress locais/de TESTE ao WP-CLI. Uma
migração de banco exige um endpoint de origem cadastrado (explícito ou descoberto de forma exata)
e endpoints de TESTE autorizados, com resolução não ambígua em ambos os lados. Credenciais do
banco de destino são entregues ao WP-CLI local por entrada padrão, e não por `argv`; credenciais
do `wp-config.php` remoto nunca são extraídas.

Com destino existente, a execução sem `--replace-existing` é recusada. Com a opção, o step
`backup_existing_test` valida novamente ambiente, estrutura, raiz permitida e ausência de symlink;
depois copia a árvore, compara uma impressão SHA-256 de conteúdo, torna o snapshot somente leitura
e publica-o com nome não colidente. Somente um registro de backup revalidado autoriza
`copy_files` a remover a árvore TESTE antiga e transferir a origem. Uma falha antes dessa validação
não remove, move nem sobrescreve o destino existente.
