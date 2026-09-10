# Requisitos de implantação ainda necessários

## PHP lado a lado

O servidor operacional de TESTE deve disponibilizar o WP-CLI no caminho absoluto configurado e
todos os binários PHP exigidos pela rota. PHP 7.4 pode coexistir com PHP 8.1, 8.2, 8.3, 8.4 ou o
runtime `current`; cada processo é invocado explicitamente como `/usr/bin/phpX.Y /caminho/wp`.
Não configure `update-alternatives` para o modernizer e não dependa do `php` global.

PHP 7.4 está EOL. Trate-o como dependência transitória isolada, obtida de uma fonte de pacotes
aprovada pela organização, e remova-o depois que todas as instalações antigas forem modernizadas,
se nenhuma outra aplicação depender dele. O modernizer não instala pacotes, não usa `sudo`, não
adiciona PPA/repositório e não modifica Apache, Nginx ou FPM.

## Executáveis verificados no preflight

O conjunto é derivado da operação e das etapas que realmente serão executadas:

| Capability | Executável | Quando é obrigatória |
|---|---|---|
| `PHP_AVAILABLE` | caminho de cada runtime PHP | diagnóstico operacional e etapa que usa WP-CLI |
| `WPCLI_AVAILABLE` | `wpcli_binary` absoluto | operações WordPress |
| `MYSQL_AVAILABLE` | `mysql` | inspeção, importação e proteção do banco |
| `MYSQLDUMP_AVAILABLE` | `mysqldump` | cópia real do banco |
| `SSH_AVAILABLE` | `ssh` | cópia real com autenticação por chave |
| `RSYNC_AVAILABLE` | `rsync` | cópia real com autenticação por chave |
| `GIT_AVAILABLE` | `git` | atualização real de plugins gerenciados configurados |

Etapas mutáveis omitidas por `--dry-run` não acrescentam dependências. O transporte SSH/tar por
senha usa Paramiko e não exige `ssh` nem `rsync` locais. A cópia exige GNU tar na origem
e permissão para executar comandos SSH (uma conta restrita a SFTP não basta). Esse requisito
remoto não é verificado pelo preflight local; falhas são reportadas como `TransferError`. Uma capability obrigatória ausente interrompe a
operação antes da criação do run e identifica seu nome no erro.

Para autenticação por chave, a inspeção da origem (inclusive no dry-run) exige `ssh`, mas
não `rsync`; `rsync` só é exigido pela cópia real. Para autenticação por senha, inspeção e cópia
usam a sessão Paramiko com verificação de host key. O servidor WordPress de PRODUÇÃO não precisa
disponibilizar `wp` nem PHP CLI: precisa permitir a leitura de `wp-config.php` por SSH/SFTP e, para a cópia por senha,
executar GNU tar em modo de leitura.
WP-CLI e PHP CLI são requisitos do servidor operacional/TESTE para diagnóstico e operações locais.
O endpoint MySQL da origem precisa aceitar as consultas `SELECT` de descoberta com uma conta
somente-leitura.

| Pergunta | Motivo | Formato esperado | Impacto se ausente |
|---|---|---|---|
| Quais são as raízes permitidas das aplicações? | delimitar caminhos destrutivos | lista YAML de caminhos absolutos | substituição desabilitada |
| Quais servidores de origem e impressões digitais SSH estão aprovados? | conexão e autenticidade do host | IDs de servidor, nomes DNS, portas e implantação de `known_hosts` | migrações indisponíveis |
| Qual provedor de segredos será usado em produção? | obter credenciais sem arquivos | nome/configuração do provedor ou política de variáveis de ambiente | apenas provedor de ambiente |
| Quais endpoints de bancos de teste são permitidos? | descoberta determinística de bancos | IDs de endpoint/DNS/portas e referências a segredos | localizador informa que não encontrou |
| Qual endpoint MySQL corresponde a cada origem? | dump somente-leitura e resolução não ambígua | literais remotos de host, banco e credenciais; acesso às portas 6612/3306 ou porta explícita | descoberta da origem é recusada |
| Bancos de teste ausentes podem ser criados? Por quem? | a criação é privilegiada/destrutiva | provisionamento prévio pela infraestrutura | nunca são criados automaticamente |
| Quais estratégias de nomes/URLs são necessárias? | convenções específicas de cada instalação | estratégia nomeada e exemplos | substituições explícitas obrigatórias |
| Qual política de proprietário/grupo/modo do sistema de arquivos se aplica? | permissões seguras após a cópia | UID/GID/modo ou adaptador de implantação | nenhuma alteração de proprietário |
| Qual revisão da política de Core/PHP foi aprovada? | compatibilidade controlada de atualização | `policy_id`, revisão e `latest_wordpress` | execução bloqueada até configurar um destino auditado |
| Quais plugins gerenciados e qual política para árvore suja se aplicam? | evitar perder trabalho local | repositório público/acessível, branch e política | atualização gerenciada ignorada |
| Onde o estado externo é mantido e copiado? | durabilidade da retomada e auditoria | diretório absoluto e política de retenção/criptografia | apenas estado local configurado |
| Quais referências de usuário e senha SSH serão provisionadas? | autenticação do transporte SSH | nomes das entradas no `SecretProvider`; nunca os valores | cópia remota indisponível |
| Qual destino de telemetria e política de dados estão aprovados? | exportação OTLP opcional | endpoint, referências de ambiente para TLS/autenticação e retenção | apenas logs JSON locais |

## Preflight SSH por senha

Antes de liberar uma origem, confirme que:

1. o DNS e a porta do servidor são alcançáveis pela conta de serviço;
2. `username_secret` e `password_secret` existem no `SecretProvider`;
3. a chave pública do host foi validada fora de banda e instalada em `~/.ssh/known_hosts` ou no
   `known_hosts_file` configurado;
4. a entrada usa o formato `[host]:porta` quando a porta não é 22;
5. a conta possui leitura e travessia sobre toda a árvore de origem;
6. a conta consegue ler `<source_path>/wp-config.php`, sem permissão para alterá-lo;
7. o destino local possui espaço para manter simultaneamente a cópia existente, o backup e a nova
   cópia;
8. o `app_root` permite criar `.wp-modernizer-backups`, e a política de retenção/backup externo foi
   definida;
9. a conta MySQL da origem consegue conectar ao schema, ler `siteurl` e gerar dump, sem privilégios de escrita;
10. um teste em infraestrutura descartável confirma as exclusões do plano e os timeouts.

Uma chave ausente ou diferente deve interromper o preflight. Não altere `host_key_policy: strict`
para resolver falhas de autenticação: confiança do host e credenciais do usuário são verificações
independentes.

Bancos de PRODUÇÃO não são cadastrados em `databases:`; esse cadastro aceita somente TESTE.

Sem porta explícita em `DB_HOST`, a conexão tenta 6612 e somente em caso de
`ENDPOINT_UNAVAILABLE` tenta 3306. `AVAILABLE` encerra a descoberta com sucesso.
`AUTHENTICATION_DENIED`, `SCHEMA_NOT_FOUND`, `CONFIGURATION_INSUFFICIENT` e `UNKNOWN`
interrompem sem fallback: trocar de serviço ocultaria uma falha que precisa ser corrigida.
Uma porta explícita válida (1–65535) é a única tentada. Falhas são sanitizadas; sockets e formatos
ambíguos são recusados.
