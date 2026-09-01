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

O adaptador público de execução delega cópias à porta de transporte remoto. Um roteador usa
SSH/rsync para autenticação por chave e SSH/SFTP (Paramiko) para autenticação por senha. A
descoberta e transferência de bancos é delegada
ao MySQL e operações WordPress ao WP-CLI. Uma migração de banco exige endpoints de origem e de
TESTE permitidos e resolução não ambígua. Credenciais do `wp-config` são entregues ao WP-CLI por
entrada padrão, e não por `argv`. A retenção de uma cópia de teste já existente continua falhando
antes de alterar estado até que um adaptador específico seja configurado.
