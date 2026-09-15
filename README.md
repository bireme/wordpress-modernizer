# wp-modernizer

O `wp-modernizer` prepara, migra, atualiza e valida cópias de
instalações WordPress em ambiente de **TESTE**, preservando o estado
da execução em caso de falha.

> **Este projeto nunca implanta em PRODUÇÃO.**

## Política de modernização WordPress

O modernizer usa uma política declarativa e auditável para impedir saltos indevidos entre versões:

| Classe | Versão detectada | Comportamento |
|---|---|---|
| `ANCIENT` | anterior a 4.9 | bloqueia automação e orienta uma ponte manual até 4.9 |
| `LEGACY` | famílias 4.9 a 6.8, incluindo patches | executa somente checkpoints posteriores à versão atual |
| `CURRENT` | família 6.9 ou posterior | segue diretamente ao WordPress `latest` aprovado |

A revisão inicial passa por WordPress 5.3 com PHP 7.4, WordPress 6.2 com PHP 7.4 e WordPress 6.8
com um runtime PHP entre 8.1 e 8.4; depois continua até `latest` usando o runtime `current`.
PHP 7.4 é transitório e está EOL. Instale-o por uma fonte aprovada e remova-o ao terminar todas as
modernizações, se nenhuma outra aplicação depender dele.

Os runtimes coexistem e são selecionados por caminho absoluto nas etapas planejadas. O WP-CLI é chamado como
`/usr/bin/php7.4 /usr/local/bin/wp ...` ou equivalente. O modernizer nunca troca o PHP global,
instala pacotes, executa `sudo`, adiciona repositórios ou modifica FPM/Apache/Nginx. `plan` apenas
inspeciona os binários com `-v` e, quando seguro, mostra sugestões informativas.

Versões anteriores a 4.9 têm rotas descritas pela documentação oficial, inclusive pontes com PHP
5.6/7.2 e migração de conteúdo para versões muito antigas. Elas ainda não são automatizadas nesta
versão porque envolvem runtimes, temas e plugins adicionais. Após concluir manualmente a ponte
até 4.9 em ambiente compatível, execute `inventory`, `diagnose` e `plan` novamente.

Referências funcionais: [guia oficial de atualização](https://developer.wordpress.org/advanced-administration/upgrade/upgrading/)
e [matriz oficial PHP/WordPress](https://make.wordpress.org/core/handbook/references/php-compatibility-and-wordpress-versions/).

## Visão geral

```mermaid
flowchart LR
    A[Origem: leitura] --> B[Backup e cópia dos arquivos]
    B --> C[Descoberta e cópia do banco]
    C --> D[Configuração de TESTE e Multisite]
    D --> E[Search-replace, HTTPS e indexação]
    E --> F[Checkpoints WordPress/PHP]
    F --> G[Plugins, temas e traduções]
    G --> H[Validação de widgets]
```

Visão resumida de `pipeline`; o runner faz sondagens e preserva o estado em caso de falha.
A [ordem detalhada](docs/operations.md#ordem-real-da-migração) inclui instalações aninhadas.

O fluxo segue o princípio de falha com preservação:

```text
PRODUÇÃO
   |
   | leitura da origem
   v
cópia de TESTE
   |
   +--> migração
   |
   +--> atualização
   |
   +--> validação
            |
            +--> sucesso
            |
            +--> falha
                   |
                   +--> parar
                   +--> preservar estado
                   +--> correção da causa externa
                   +--> resume se consistente
```

> **O wp-modernizer nunca implanta em PRODUÇÃO.**
>
> O modelo de domínio rejeita destinos de produção e a aplicação não oferece comandos para promover, sincronizar, publicar ou implantar uma instalação de TESTE em PRODUÇÃO.

## Principais características

* migração de instalações WordPress de PRODUÇÃO para TESTE;
* descoberta controlada das informações da instalação de origem;
* cópia de arquivos e banco de dados;
* atualização da instalação de TESTE;
* planejamento antes da execução;
* modo `--dry-run`;
* validação por capacidades;
* checkpoints entre etapas;
* preservação do estado em caso de falha;
* retomada com `resume`;
* proteção contra operações destrutivas em caminhos não autorizados;
* backup da cópia de TESTE antes de substituí-la;
* suporte a SSH por chave ou senha;
* referências a secrets e sanitização de logs;
* arquitetura baseada em portas e adaptadores;
* testes unitários independentes de WordPress, MySQL, SSH e WP-CLI.

---

## Requisitos

### Python

O projeto requer:

```text
Python >= 3.10
```

### Ambiente operacional

Para execuções reais, o servidor operacional/de TESTE precisa disponibilizar a infraestrutura necessária às operações que serão executadas, incluindo, conforme o caso:

* PHP CLI;
* WP-CLI;
* cliente MySQL;
* acesso aos bancos de TESTE;
* SSH/rsync para autenticação por chave; ou
* SSH/tar via Paramiko para autenticação por senha;
* acesso de leitura à instalação WordPress de origem;
* acesso de escrita à instalação de TESTE;
* diretório persistente e gravável para o estado do modernizer.

WordPress, MySQL, SSH, PHP e WP-CLI **não são necessários para executar os testes unitários** do projeto.

---

## Instalação

Clone o repositório:

```bash
git clone https://github.com/bireme/wordpress-modernizer.git
cd wordpress-modernizer
```

Crie um ambiente virtual:

```bash
python -m venv .venv
```

Ative-o:

```bash
. .venv/bin/activate
```

Instale o projeto com as dependências de desenvolvimento:

```bash
python -m pip install -e '.[dev]'
```

O comando principal ficará disponível como:

```bash
wp-modernizer
```

---

## Configuração inicial

Crie a configuração local a partir do exemplo:

```bash
cp config.example.yaml config.yaml
```

O `config.yaml` contém informações específicas do ambiente e do servidor e **não deve ser versionado**.

Valores secretos não devem ser gravados diretamente no YAML. O arquivo de configuração utiliza referências a variáveis de ambiente, resolvidas pelo `EnvironmentSecretProvider`.

Exemplo conceitual:

```yaml
servers:
  source-example:
    host: source.example.org
    port: 22
    environment: production
    username_secret: PROD_EXAMPLE_USERNAME
    authentication: password
    password_secret: PROD_EXAMPLE_PASSWORD
    host_key_policy: strict
```

Os valores reais são fornecidos pelo ambiente:

```bash
export PROD_EXAMPLE_USERNAME='usuario'
export PROD_EXAMPLE_PASSWORD='senha'
```

Consulte [`docs/configuration.md`](docs/configuration.md) para a estrutura completa da configuração.

---

## `config.yaml` e `plugins.yaml`

Os dois arquivos possuem responsabilidades diferentes.

### `config.yaml`

É local e específico do servidor.

Pode conter:

* instalações;
* servidores;
* caminhos permitidos;
* endpoints de bancos de TESTE;
* aliases e overrides;
* referências a secrets;
* diretório de estado;
* opções específicas da infraestrutura.

Ele não deve ser versionado.

### `plugins.yaml`

Contém a lista pública e compartilhada de plugins gerenciados pelo modernizer.

Esse arquivo:

* acompanha a aplicação;
* é versionado;
* possui localização fixa;
* é carregado automaticamente;
* não pode ter seu caminho substituído por argumento de linha de comando, variável de ambiente ou configuração YAML.

Cada plugin gerenciado é validado antes da execução.

`strategy: replace_from_git` mantém a substituição por clone em staging, com as
validações e rollback existentes. `strategy: update_from_git` atualiza o checkout
existente por `fetch` da branch configurada e `merge --ff-only`, sem criar merge
commits. Exige checkout na branch configurada, remote `origin` correspondente ao
`repository` e ausência de operações Git pendentes. URLs HTTPS e SSH convencionais
podem representar o mesmo repositório; outro remote não é corrigido automaticamente.

As políticas para modificações locais são:

* `abort`: preserva o conteúdo e retorna `FAILED_PRESERVED`, interrompendo os plugins seguintes.
* `skip`: preserva e ignora explicitamente o plugin, continuando o processamento.
* `stash`: em `update_from_git`, salva arquivos tracked e untracked com
  `git stash push --include-untracked -m "wp-modernizer <run-id>"` e tenta reaplicá-los
  com `stash apply --index`. O stash é mantido como cópia de recuperação mesmo após sucesso.

Conflitos na reaplicação exigem intervenção manual e retornam `FAILED_PRESERVED`.
Não há resolução automática, reset ou limpeza do checkout. Se fetch ou fast-forward
falhar, as alterações permanecem no stash identificado na mensagem; inspecione
`git status` e `git stash list` antes de recuperar manualmente. Um stash já reaplicado
não deve ser aplicado novamente. Arquivos ignorados pelo Git não entram nesse stash.

Plugins inexistentes usam o mesmo clone/staging da substituição. Diretórios existentes
não Git, inválidos ou com metadados externos não são convertidos destruindo conteúdo:
as regras conservadoras do fallback recusam a substituição (`FAILED_PRESERVED`, ou
`SKIPPED` para diretório não verificável com política `skip`). `replace_from_git` com
`stash` também preserva e recusa um diretório sujo, pois substituir o checkout eliminaria
seus metadados locais; use `update_from_git` para reaplicar modificações. Todas as
operações continuam restritas ao ambiente TEST e ao diretório autorizado de plugins.


---

## Conceitos de segurança

### PRODUÇÃO é somente origem

Uma instalação configurada como PRODUÇÃO pode ser utilizada como fonte para leitura e migração.

O modernizer não executa sobre ela:

* atualização WordPress;
* escrita de banco;
* WP-CLI remoto;
* PHP remoto;
* publicação;
* sincronização reversa;
* implantação.

A inspeção da origem lê diretamente o `wp-config.php` através do transporte remoto autorizado e extrai os valores necessários sem executar o WordPress.

### TESTE é o único destino mutável

Endpoints configurados em `databases:` precisam representar ambientes de TESTE.

`allowed_database_endpoints` funciona como uma allowlist destrutiva e também aceita somente endpoints de TESTE.

### Segredos da origem são efêmeros

Os valores:

```text
DB_USER
DB_PASSWORD
```

obtidos para a conexão efêmera de PRODUÇÃO não são acrescentados aos metadados de conexão em:

* logs;
* exceções;
* estado;
* manifestos;
* recovery data;
* argumentos de subprocessos.

Essa proteção não torna os artefatos livres de secrets: a cópia/backup de `wp-config.php`
e opções do site podem conter dados sensíveis; veja [segurança](docs/security.md).

Quando o cliente MySQL precisa utilizá-los, eles são fornecidos por arquivo temporário protegido e removido depois da operação.

---

## Fluxo recomendado

Para uma instalação chamada `example-site`, o fluxo operacional recomendado é:

```text
inventory
    |
diagnose
    |
plan
    |
pipeline --dry-run
    |
revisão
    |
pipeline
```

### 1. Inspecionar a instalação

```bash
wp-modernizer --config config.yaml inventory example-site
```

`inventory` combina diagnóstico do destino local com caminhos e dados configurados.
Versões, tema ativo, lista de plugins, URL e contagens de widgets ainda são campos
`indisponível`, mesmo quando há capacidades disponíveis; não é um inventário completo da origem.

### 2. Diagnosticar capacidades

```bash
wp-modernizer --config config.yaml diagnose example-site
```

`diagnose` verifica capacidades e integridade do ambiente.

Isso ajuda a identificar problemas locais como ausência de PHP ou WP-CLI. O diagnóstico
independente usa `php` do PATH; `plan` inspeciona os runtimes explícitos da rota.

### 3. Gerar o plano

```bash
wp-modernizer --config config.yaml plan example-site
```

Para obter saída estruturada:

```bash
wp-modernizer --config config.yaml plan example-site --json
```

`plan` mostra o que o modernizer pretende executar sem alterar a instalação.

O planejamento inclui, conforme aplicável:

* etapas;
* dependências;
* pontos de controle;
* rota WordPress/PHP e prontidão dos runtimes;
* caminhos;
* exclusões de cópia;
* trabalho pendente;
* resolução de instalações aninhadas.

A descoberta efetiva de banco e URL ocorre em `snapshot_source_database`, durante
`migrate`/`pipeline`, inclusive no dry-run quando as capacidades necessárias estão disponíveis.
`plan` não executa essa etapa nem resolve os bancos de origem/destino.

### 4. Validar com dry-run

```bash
wp-modernizer --config config.yaml pipeline example-site --dry-run
```

O `--dry-run` garante que o destino não seja alterado.

Ele não significa simplesmente adicionar `--dry-run` aos comandos externos. Cada operação do modernizer possui uma classificação explícita de segurança.

### 5. Executar

Depois de revisar o inventário, diagnóstico, plano e dry-run:

```bash
wp-modernizer --config config.yaml pipeline example-site
```

---

## Comandos

| Comando     | Finalidade                                         | Altera TESTE? |
| ----------- | -------------------------------------------------- | ------------: |
| `inventory` | Coleta informações da instalação                   |           Não |
| `diagnose`  | Verifica capacidades e integridade                 |           Não |
| `plan`      | Calcula e apresenta o plano                        |           Não |
| `migrate`   | Prepara arquivos e banco da cópia de TESTE         |           Sim |
| `update`    | Executa as etapas de atualização da cópia de TESTE |           Sim |
| `pipeline`  | Executa migração + atualização                     |           Sim |
| `resume`    | Retoma uma execução interrompida                   |           Sim |

Todos os comandos aceitam saída JSON quando aplicável:

```bash
wp-modernizer --config config.yaml inventory example-site --json
```

---

## `--dry-run`

O dry-run é uma proteção global.

Exemplo:

```bash
wp-modernizer --config config.yaml pipeline example-site --dry-run
```

Durante a simulação, cada etapa é classificada de acordo com sua capacidade.

### `READ_ONLY`

A operação pode ser executada porque não altera estado.

Resultado:

```text
VALIDATED
```

### `MUTABLE_WITHOUT_SAFE_DRY_RUN`

A operação seria mutável, mas não possui uma simulação considerada segura.

Ela **não é executada**.

Resultado:

```text
PLANNED
```

### `MUTABLE_WITH_NATIVE_DRY_RUN`

A operação possui uma forma de validação nativa explicitamente revisada e autorizada.

Resultado:

```text
VALIDATED
```

Uma opção chamada `--dry-run` em uma ferramenta externa não é suficiente para o modernizer considerá-la segura automaticamente.

Cada operação precisa ser explicitamente classificada.

Uma execução real bem-sucedida fica:

```text
EXECUTED
```

---

## Migração

O comando:

```bash
wp-modernizer --config config.yaml migrate example-site
```

prepara a cópia de TESTE.

A migração faz backup e cópia dos arquivos, descobre banco/URL, importa o banco,
configura a conexão de TESTE e aplica correções Multisite, search-replace, HTTPS e indexação.
A [ordem das etapas](docs/operations.md#ordem-real-da-migração) também explica o processamento de instalações aninhadas.

---

## Substituição de uma instalação de TESTE existente

Uma instalação já existente não é substituída implicitamente.

Sem autorização explícita, a operação é recusada.

Para permitir a substituição:

```bash
wp-modernizer --config config.yaml migrate example-site --replace-existing
```

ou, no fluxo completo:

```bash
wp-modernizer --config config.yaml pipeline example-site --replace-existing
```

Antes de remover a instalação existente, o modernizer precisa criar e validar um backup dela.

O backup fica fora do `htdocs`:

```text
<app_root>/.wp-modernizer-backups/<run-id>/<installation-id>/
```

Esse backup cobre a árvore de arquivos; não inclui um dump do banco de TESTE anterior.

O snapshot:

* utiliza um caminho novo para cada execução;
* não sobrescreve backups anteriores;
* preserva a árvore de arquivos;
* tem seu conteúdo verificado;
* torna-se somente leitura;
* é revalidado antes da substituição destrutiva.

A instalação existente só pode ser removida depois que o backup for considerado válido.

---

## Bancos de dados

### Descoberta da origem

O banco de PRODUÇÃO não precisa ser cadastrado em `databases:`.

O modernizer descobre diretamente no `wp-config.php` remoto:

```text
DB_NAME
DB_HOST
DB_USER
DB_PASSWORD
$table_prefix
```

Depois consulta `siteurl` diretamente no banco da origem através de uma operação de leitura.

A conta MySQL utilizada em PRODUÇÃO deve ser somente leitura.

### Porta MySQL

Quando `DB_HOST` não informa uma porta explícita, o modernizer tenta:

```text
6612
```

e somente em caso de indisponibilidade do endpoint tenta:

```text
3306
```

Falhas como autenticação negada ou schema inexistente interrompem a descoberta em vez de provocar fallback silencioso.

### Banco de destino

Os bancos cadastrados em:

```yaml
databases:
```

são exclusivamente endpoints controlados de TESTE.

O modernizer não cria bancos automaticamente.

Schemas precisam ser provisionados previamente pela infraestrutura.

---

## Resolução automática do banco de TESTE

Quando o banco de origem possui exatamente o formato:

```text
wp_<name>_prod
```

o modernizer considera como candidato:

```text
wp_<name>_tst
```

A comparação é exata.

Não existe busca por similaridade.

Quando necessário, a instalação pode utilizar:

```yaml
database_override:
```

ou aliases explicitamente configurados.

Ausência ou ambiguidade interrompe a operação para correção pela infraestrutura.

---

## Caminhos e domínio

O domínio é inferido individualmente a partir de `source_path`, relativamente a uma entrada de `allowed_app_roots`.

Exemplo:

```yaml
allowed_app_roots:
  - /home/apps

source_path: /home/apps/example.org/wp-example/htdocs
```

resulta no domínio:

```text
example.org
```

`destination_path` é opcional.

Quando omitido, o modernizer utiliza o mesmo caminho da origem no servidor operacional de TESTE.

Um caminho explícito pode ser configurado para instalações excepcionais.

---

## URL de TESTE

A URL de TESTE é derivada do domínio inferido.

Exemplos:

```text
https://boletin.bireme.org
```

torna-se:

```text
https://boletin.teste.bireme.org
```

Enquanto:

```text
https://bireme.org
```

torna-se:

```text
https://teste.bireme.org
```

O path da URL é preservado.

Uma instalação excepcional pode utilizar:

```yaml
test_url:
```

O valor explícito tem precedência, mas não pode reutilizar o hostname de PRODUÇÃO.

---

## Atualização

O comando:

```bash
wp-modernizer --config config.yaml update example-site
```
executa as etapas de modernização e atualização definidas para a cópia de TESTE.

Em cada checkpoint de Core, o modernizer:

* verifica a versão atual;
* atualiza para a versão exata planejada, quando necessário;
* executa a atualização do banco do WordPress;
* confirma a versão resultante;
* verifica os checksums do Core;
* valida um bootstrap reduzido antes de considerar o checkpoint concluído.

Depois dos checkpoints de Core, o pipeline executa, conforme aplicável:

* atualização dos plugins gerenciados;
* atualização dos demais plugins;
* atualização dos temas;
* atualização das traduções do Core;
* atualização das traduções dos plugins;
* atualização das traduções dos temas;
* validação dos widgets.

O comando não deve ser executado diretamente em uma instalação de PRODUÇÃO.

Para verificar previamente o comportamento planejado:

```bash
wp-modernizer --config config.yaml update example-site --dry-run
```

> A sequência operacional detalhada e as capacidades de dry-run de cada etapa estão documentadas em [`docs/operations.md`](docs/operations.md).

---

## Pipeline

O comando mais comum para uma execução completa é:

```bash
wp-modernizer --config config.yaml pipeline example-site
```

Ele concatena:

```text
migração
   +
atualização
```

e preserva os checkpoints necessários para recuperação.

Antes da primeira execução real, recomenda-se:

```bash
wp-modernizer --config config.yaml pipeline example-site --dry-run
```

---

## Falhas e recuperação

O modernizer adota o princípio de:

```text
falhar -> parar -> preservar -> investigar -> corrigir -> retomar
```

Uma falha **não provoca rollback global do pipeline**. A substituição de um plugin
por staging possui recuperação local própria; veja as políticas de plugins gerenciados.

Isso é intencional.

Quando uma etapa falha, a instalação de TESTE permanece disponível para investigação.

O estado externo registra informações como:

* última etapa bem-sucedida;
* etapa que falhou;
* detalhes fatais sanitizados;
* checkpoints;
* operações pendentes;
* integridade antes e depois;
* diferenças relevantes;
* fingerprint da instalação.

Após investigar e corrigir a causa externa, use o ID do manifesto preservado (substitua
o valor entre aspas) para tentar a retomada:

```bash
wp-modernizer --config config.yaml resume example-site --run-id "<run-id>"
```

O `resume` não continua cegamente.

Ele compara a instalação atual com o estado registrado e exige consistência antes de continuar.

Alterações nos arquivos podem mudar o fingerprint e bloquear `resume`. Não existe opção
para aceitar um fingerprint novo; nesse caso pode ser necessária uma nova execução.
Mudanças no banco e em `plugins.yaml` têm regras próprias descritas em [recuperação](docs/recovery.md).

### Restauração explícita de widgets

O modernizer preserva e compara o estado dos widgets durante a atualização.

Quando uma recuperação exigir explicitamente a restauração do snapshot de widgets registrado pela
execução, utilize:

```bash
wp-modernizer --config config.yaml resume example-site --run-id "<run-id>" --restore-widgets
```
A restauração não ocorre implicitamente durante todo `resume`. A opção
`--restore-widgets` deve ser fornecida explicitamente quando essa intervenção fizer parte da
recuperação planejada.

Consulte [`docs/recovery.md`](docs/recovery.md) antes de restaurar backups, restaurar widgets ou realizar outras
alterações manuais em uma execução preservada.

---

## Códigos de saída

Uma conclusão normal retorna `0`. Para `migrate`, `update` e `pipeline`, o CLI retorna
explicitamente `2` quando o service **devolve** um manifesto `UPDATE_FAILED_PRESERVED`.
Exceções propagadas seguem o tratamento de erros do CLI; não há um código próprio
contratado para cada tipo de falha. `resume` atualmente não aplica a conversão explícita
para `2`: verifique também o status do manifesto/JSON. Nenhum código autoriza apagar a cópia.

---

## Diretório de estado

`state_directory` é parte essencial do mecanismo de preservação e `resume`.

Antes de uma operação mutável, o modernizer verifica se consegue:

```text
criar
escrever
ler
```

o diretório de estado.

Se isso falhar, a operação mutável é recusada antes de modificar o destino.

### Containers

Quando o modernizer é executado em container, `state_directory` deve ficar em um **volume persistente**.

Não utilize apenas o filesystem efêmero do container.

Caso contrário, a recriação do container pode eliminar os manifestos e checkpoints necessários à recuperação.

---

## Observabilidade

O CLI grava logs JSON Lines em `<state_directory>/logs/` para `diagnose`, `migrate`,
`update`, `pipeline` e `resume`, inclusive em dry-run. Manifestos e checkpoints JSON ficam
separadamente em `<state_directory>/<installation-id>/runs/<run-id>/`.

Os campos variam por evento; não há contexto de correlação automático em todos eles.
Logs podem conter credenciais em saídas não mascaradas; veja [segurança](docs/security.md).
Existe uma abstração interna de métricas em memória, mas o extra `otel` não ativa exportação:
OpenTelemetry/OTLP e variáveis `OTEL_*` não estão integrados ao fluxo operacional.

Consulte [observabilidade](docs/observability.md) para formatos, campos e limites.

---

## Transporte SSH

Cada servidor escolhe explicitamente seu método de autenticação.

### Senha

```yaml
authentication: password
```

Utiliza um stream de GNU tar por SSH através do Paramiko para copiar árvores,
preservando inclusive nomes Unix com bytes inválidos em UTF-8. Requer GNU tar na origem.
SFTP é usado para ler `wp-config.php` e `wp-includes/version.php`.

Usuário e senha são obtidos somente no momento da conexão.

### Chave SSH

```yaml
authentication: key
```

Utiliza OpenSSH/rsync.

### Host keys

Em ambientes reais recomenda-se:

```yaml
host_key_policy: strict
```

Nesse modo, chaves desconhecidas ou alteradas são rejeitadas.

Também é possível indicar um arquivo de host keys (adicional no Paramiko;
`UserKnownHostsFile` no OpenSSH):

```yaml
known_hosts_file: /etc/wp-modernizer/known_hosts
```

Esse arquivo deve ser provisionado previamente por um canal confiável.

---

## Instalações aninhadas

O planner trata instalações pai/filho explicitamente.

As instalações filhas precisam estar cadastradas em `installations`; não há descoberta
recursiva de instalações não cadastradas. O plano calcula exclusões de cópia para as filhas
selecionadas sob o caminho de destino do pai. A substituição remove a árvore antiga inteira
após o backup; as exclusões delimitam a transferência, não preservam a filha antiga no local.

Cada instalação continua sendo processada por suas próprias etapas.

---

## Proteções da cópia de TESTE

Além de impedir operações mutáveis em PRODUÇÃO, o fluxo de migração aplica proteções específicas
à cópia de TESTE antes das etapas de modernização e atualização.

### HTTPS em TESTE

Durante a migração, as referências HTTP dos hosts próprios da cópia de TESTE são normalizadas para
HTTPS.

O planejamento e a execução dessa transformação fazem parte do estado persistido da execução para
permitir validação e retomada consistentes.

Consulte [`docs/test-https.md`](docs/test-https.md) para o comportamento completo.

### Indexação por mecanismos de busca

Após a normalização HTTPS, o modernizer configura:

```text
blog_public=0
```

Em instalações single-site e em todos os blogs relevantes de instalações Multisite.

Essa configuração desencoraja mecanismos de busca a indexarem a cópia de TESTE, mas não constitui
um bloqueio absoluto contra crawlers.

A operação possui planejamento persistido e comportamento idempotente para permitir retomada
segura.

Consulte [`docs/test-indexing.md`](docs/test-indexing.md).

---

## Arquitetura

O projeto segue uma arquitetura de **portas e adaptadores**.

Estrutura conceitual:

```text
src/wp_modernizer/
├── domain/
├── application/
├── pipeline/
├── infrastructure/
└── cli/
```

### `domain`

Contém:

* modelos;
* enums;
* invariantes;
* análise de caminhos;
* nomenclatura;
* planejamento.

Não depende de APIs de processos externos.

### `application`

Contém:

* casos de uso;
* portas definidas por `Protocol`.

### `pipeline`

Contém:

* etapas independentes;
* executor;
* checkpoints;
* preservação de estado.

### `infrastructure`

Contém os adaptadores concretos para:

* subprocessos;
* filesystem;
* estado;
* YAML;
* variáveis de ambiente;
* MySQL;
* SSH;
* SFTP;
* rsync;
* WP-CLI;
* Git.

### `cli`

É responsável pela composição da aplicação.

As dependências apontam para dentro, permitindo testar as regras de domínio e segurança com objetos falsos sem exigir infraestrutura WordPress real.

---

## Desenvolvimento

Instale as dependências:

```bash
python -m pip install -e '.[dev]'
```

### Testes

```bash
pytest
```

Os testes marcados como integração são excluídos da execução padrão.

O teste externo atual é um placeholder e sempre é pulado, mesmo com opt-in.
Uma integração WordPress/MySQL real ainda exige fixtures de laboratório; veja [desenvolvimento](docs/development.md).

### Cobertura

Quando a cobertura é coletada (`pytest --cov`, como na CI), o limite mínimo é:

```text
80%
```

### Ruff

Verifique o código:

```bash
ruff check .
```

Verifique formatação:

```bash
ruff format --check .
```

### Mypy

Execute a análise estática:

```bash
mypy src
```

O projeto utiliza configuração `strict`.

### Verificação recomendada antes de commit

```bash
pytest
ruff check .
ruff format --check .
mypy src
```

---

## Início rápido

Para um ambiente já configurado:

```bash
python -m venv .venv
. .venv/bin/activate

python -m pip install -e '.[dev]'

cp config.example.yaml config.yaml
```

Configure os secrets necessários e então:

```bash
wp-modernizer --config config.yaml inventory example-site
wp-modernizer --config config.yaml diagnose example-site
wp-modernizer --config config.yaml plan example-site
wp-modernizer --config config.yaml pipeline example-site --dry-run
```

Depois de revisar o resultado:

```bash
wp-modernizer --config config.yaml pipeline example-site
```

Se uma execução falhar:

```text
investigue a cópia preservada
        |
corrija a causa externa
        |
confirme configuração e fingerprint consistentes
        |
tente resume (ou inicie nova execução se houver divergência)
```

```bash
wp-modernizer --config config.yaml resume example-site --run-id "<run-id>"
```

---

## Documentação

A documentação detalhada está separada por responsabilidade.

### [Configuração](docs/configuration.md)

Detalha:

* `config.yaml`;
* `plugins.yaml`;
* servidores;
* secrets;
* bancos;
* URLs;
* caminhos;
* autenticação SSH;
* `state_directory`.

### [Operações](docs/operations.md)

Detalha:

* comandos;
* pipeline;
* capacidades;
* dry-run;
* migração;
* resolução de banco;
* execução.

### [Recuperação](docs/recovery.md)

Detalha:

* preservação em caso de falha;
* checkpoints;
* backups;
* manifestos;
* fingerprints;
* `resume`;
* recuperação manual.

### [Arquitetura](docs/architecture.md)

Detalha:

* domínio;
* aplicação;
* pipeline;
* infraestrutura;
* portas;
* adaptadores;
* composition root;
* separação das responsabilidades.

### [Requisitos de implantação](docs/deployment-requirements.md)

Detalha:

* runtimes PHP necessários;
* executáveis e capabilities de preflight;
* requisitos de SSH e MySQL;
* infraestrutura que precisa ser provisionada externamente;
* requisitos operacionais antes de liberar uma instalação.

### [Segurança](docs/security.md)

Detalha:

* fronteiras entre PRODUÇÃO e TESTE;
* proteção de caminhos e endpoints;
* tratamento de segredos;
* segurança dos subprocessos;
* transporte SSH;
* validações durante cópia e extração de arquivos.

### [Multisite](docs/multisite.md)

Detalha o comportamento específico para instalações WordPress Multisite e suas transformações.

### [Observabilidade](docs/observability.md)

Detalha:

* logs e relatórios estruturados;
* correlação por `run_id`;
* métricas;
* limites atuais: sem exportação OpenTelemetry/OTLP.

### [Desenvolvimento](docs/development.md)

Detalha o ambiente de desenvolvimento, verificações locais e procedimentos destinados a
contribuidores.

### [HTTPS em TESTE](docs/test-https.md)

Detalha a normalização HTTPS aplicada às cópias de TESTE.

### [Indexação em TESTE](docs/test-indexing.md)

Detalha a proteção contra indexação das cópias de TESTE.

---

## Princípios operacionais

O comportamento do projeto pode ser resumido por estas regras:

```text
PRODUÇÃO é origem, nunca destino.

TESTE é o único ambiente mutável.

Planejar antes de alterar.

Dry-run nunca deve alterar o destino.

Falhar significa preservar, não esconder a falha com rollback automático.

Uma operação destrutiva exige evidência e autorização explícitas.

Segredos não pertencem ao estado nem aos logs.

Resume valida o estado antes de continuar.
```

---

## Licença

A seleção da licença do projeto ainda depende de aprovação organizacional.
