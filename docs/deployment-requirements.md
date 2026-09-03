# Requisitos de implantação ainda necessários

## Executáveis verificados no preflight

O conjunto é derivado da operação e das etapas que realmente serão executadas:

| Capability | Executável | Quando é obrigatória |
|---|---|---|
| `PHP_AVAILABLE` | `php` | diagnóstico operacional e fluxos que usam WP-CLI |
| `WPCLI_AVAILABLE` | `wp` | operações WordPress |
| `MYSQL_AVAILABLE` | `mysql` | inspeção, importação e proteção do banco |
| `MYSQLDUMP_AVAILABLE` | `mysqldump` | cópia real do banco |
| `SSH_AVAILABLE` | `ssh` | cópia real com autenticação por chave |
| `RSYNC_AVAILABLE` | `rsync` | cópia real com autenticação por chave |
| `GIT_AVAILABLE` | `git` | atualização real de plugins gerenciados configurados |

Etapas mutáveis omitidas por `--dry-run` não acrescentam dependências. O transporte SFTP por
senha usa Paramiko e não exige `ssh` nem `rsync`. Uma capability obrigatória ausente interrompe a
operação antes da criação do run e identifica seu nome no erro.

Para autenticação por chave, a inspeção WordPress remota (inclusive no dry-run) exige `ssh`, mas
não `rsync`; `rsync` só é exigido pela cópia real. Para autenticação por senha, inspeção e cópia
usam a sessão Paramiko com verificação de host key. O servidor remoto precisa disponibilizar
`wp` para a conta configurada. A validação final dessa disponibilidade ocorre na leitura remota,
sem executar operação mutável.

| Pergunta | Motivo | Formato esperado | Impacto se ausente |
|---|---|---|---|
| Quais são as raízes permitidas das aplicações? | delimitar caminhos destrutivos | lista YAML de caminhos absolutos | substituição desabilitada |
| Quais servidores de origem e impressões digitais SSH estão aprovados? | conexão e autenticidade do host | IDs de servidor, nomes DNS, portas e implantação de `known_hosts` | migrações indisponíveis |
| Qual provedor de segredos será usado em produção? | obter credenciais sem arquivos | nome/configuração do provedor ou política de variáveis de ambiente | apenas provedor de ambiente |
| Quais endpoints de bancos de teste são permitidos? | descoberta determinística de bancos | IDs de endpoint/DNS/portas e referências a segredos | localizador informa que não encontrou |
| Qual endpoint MySQL corresponde a cada origem? | dump somente-leitura e resolução não ambígua | correspondência exata de `DB_HOST` ou `source_database_endpoint` | descoberta da origem é recusada |
| Bancos de teste ausentes podem ser criados? Por quem? | a criação é privilegiada/destrutiva | booleano por endpoint e processo de concessão | nunca são criados automaticamente |
| Quais estratégias de nomes/URLs são necessárias? | convenções específicas de cada instalação | estratégia nomeada e exemplos | substituições explícitas obrigatórias |
| Qual política de proprietário/grupo/modo do sistema de arquivos se aplica? | permissões seguras após a cópia | UID/GID/modo ou adaptador de implantação | nenhuma alteração de proprietário |
| Quais pontos de controle do núcleo são aceitos por site? | compatibilidade controlada de atualização | lista ordenada de versões | apenas etapas genéricas configuradas são executadas |
| Quais plugins gerenciados e qual política para árvore suja se aplicam? | evitar perder trabalho local | repositório público/acessível, branch e política | atualização gerenciada ignorada |
| Onde o estado externo é mantido e copiado? | durabilidade da retomada e auditoria | diretório absoluto e política de retenção/criptografia | apenas estado local configurado |
| Quais referências de usuário e senha SSH serão provisionadas? | autenticação do transporte SFTP | nomes das entradas no `SecretProvider`; nunca os valores | cópia remota indisponível |
| Qual destino de telemetria e política de dados estão aprovados? | exportação OTLP opcional | endpoint, referências de ambiente para TLS/autenticação e retenção | apenas logs JSON locais |

## Preflight SSH por senha

Antes de liberar uma origem, confirme que:

1. o DNS e a porta do servidor são alcançáveis pela conta de serviço;
2. `username_secret` e `password_secret` existem no `SecretProvider`;
3. a chave pública do host foi validada fora de banda e instalada em `~/.ssh/known_hosts` ou no
   `known_hosts_file` configurado;
4. a entrada usa o formato `[host]:porta` quando a porta não é 22;
5. a conta possui leitura e travessia sobre toda a árvore de origem;
6. a conta consegue executar `wp --path=<source_path> config get` e `wp option get` somente para
   inspeção;
7. o destino local possui espaço para manter simultaneamente a cópia existente, o backup e a nova
   cópia;
8. o `app_root` permite criar `.wp-modernizer-backups`, e a política de retenção/backup externo foi
   definida;
9. um teste em infraestrutura descartável confirma as exclusões do plano e os timeouts.

Uma chave ausente ou diferente deve interromper o preflight. Não altere `host_key_policy: strict`
para resolver falhas de autenticação: confiança do host e credenciais do usuário são verificações
independentes.
