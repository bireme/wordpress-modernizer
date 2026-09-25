# Recuperação e preservação em caso de falha

A rota de modernização completa fica no manifesto: classe e versão WordPress inicial, identidade
e revisão da política, checkpoints ordenados, requisitos PHP, binários selecionados e
`planned_steps`. O histórico de steps registra os checkpoints concluídos; `last_successful_step` e
`failed_step` identificam a posição atual. `resume` usa exatamente essa lista persistida, mesmo que
a política instalada tenha evoluído, e verifica novamente o runtime exigido antes da próxima
etapa restante. Um binário removido, de família diferente da configurada ou incompatível
com o requisito bloqueia a retomada. Um patch PHP novo da mesma família pode ser aceito.

Manifestos antigos que não possuem seleção PHP explícita continuam legíveis para auditoria, mas
não podem retomar uma rota modernizada com segurança; gere um novo plano/run.

O pipeline não reverte a execução inteira após uma falha. A substituição de plugins por
staging pode desfazer localmente uma troca que falhou; isso não é rollback da atualização.
O executor registra em estado externo a última
etapa bem-sucedida, a etapa que falhou, a integridade antes/depois, detalhes fatais, pontos de
controle, diferenças de widgets, operações pendentes e uma impressão digital do sistema de
arquivos. Investigar manualmente é permitido. Corrigir uma causa externa (por exemplo,
restabelecer um serviço) pode permitir `resume`, desde que as verificações continuem válidas.
Editar arquivos da instalação pode alterar o fingerprint e **bloqueia** a retomada quando
há divergência. Não há opção para aceitar um fingerprint novo; uma nova execução pode ser necessária.

```bash
wp-modernizer --config config.yaml resume example-site --run-id "<run-id>"
```

Substitua o valor entre aspas pelo ID preservado. `resume` cria outro run, mantendo
`original_run_id` e `resumed_from_run_id`, e repete a partir da primeira etapa não concluída.
Resultados apenas `PLANNED`/`VALIDATED` de um dry-run não contam como execução concluída.

## O que é comparado na retomada

- **Arquivos:** `LocalFileSystem.fingerprint` usa SHA-256 de caminhos relativos, tamanhos e
  `mtime_ns` dos arquivos, ignorando diretórios `.git`. Não calcula hash de conteúdo nem inclui
  permissões ou diretórios vazios. Uma alteração pode ser detectada até sem mudar o conteúdo;
  esse mecanismo não é uma verificação integral de integridade. O fingerprint de backup,
  por sua vez, verifica conteúdo e é distinto deste.
- **Configuração:** o snapshot crítico compara o modelo completo, exceto `managed_plugins`,
  `observability`, `state_directory` e `latest_wordpress`. Mudanças em caminhos, servidores,
  endpoints, referências de secrets ou `php_runtimes` bloqueiam. Os valores dos secrets de
  ambiente não fazem parte dessa comparação. Mudar `latest_wordpress` não muda a rota persistida.
- **Banco:** não entra no fingerprint de arquivos. As etapas Multisite/HTTPS/indexação verificam
  seus planos e aceitam somente os estados previstos. Correções manuais arbitrárias no banco
  não têm garantia de retomada; etapas já concluídas são puladas.
- **Plugins gerenciados:** quando a falha foi em `managed_plugin_refresh` e essa é a primeira etapa
  restante, a lista atual de `plugins.yaml` é reconciliada com a original, registrando inclusões,
  remoções e mudanças. Nos demais casos vale a lista persistida. Isso não dispensa a comparação
  do filesystem: editar arquivos do checkout ou reaplicar stash pode impedir `resume`.

Uma interrupção abrupta pode deixar apenas o último manifesto salvo, sem fingerprint final.
O runner só compara fingerprints quando há um persistido; não se deve interpretar essa ausência
como prova de que os arquivos permaneceram inalterados.

O `state_directory` é parte obrigatória desse mecanismo. Toda operação mutável faz preflight de
criação (quando necessária), escrita e leitura do diretório e não começa se o teste falhar. Em
container, monte esse caminho em **volume persistente**; armazená-lo apenas no filesystem efêmero
do container elimina os manifestos e checkpoints necessários ao `resume` quando o container é
substituído. O preflight valida acesso real, mas não tenta adivinhar se um filesystem qualquer é
efêmero.

Para abandonar uma execução, inicie uma nova migração com `--replace-existing`. Uma cópia de
TESTE existente deve ser salva com sucesso antes que o adaptador destrutivo possa removê-la. As
proteções de analisador, lista de permissão, caminho exato, ambiente e link simbólico devem ser
aprovadas. Caminhos de produção não passam por essa proteção.

## Backup de substituição

O backup fica no `app_root` validado pelo analisador, fora de `htdocs`:

```text
<app_root>/.wp-modernizer-backups/<run-id>/<installation-id>/
```

O backup contém arquivos, inclusive `wp-config.php`; **não inclui dump do banco anterior**.
A cópia do banco importa a origem no schema de TESTE sem salvar automaticamente esse schema.
A restauração integral do ambiente anterior depende também de um backup externo do banco.

Os componentes são normalizados para nomes simples. Cada run usa um caminho novo; um backup
existente nunca é sobrescrito. A árvore é copiada preservando metadados e links simbólicos, tem o
conteúdo comparado antes/depois da cópia, recebe bits de escrita removidos e é revalidada antes de
o destino original poder ser removido. O diretório pai permanece administrável para permitir uma
política externa de retenção; “imutável” aqui significa snapshot não colidente e somente leitura,
não atributo de filesystem (`chattr`).

O `manifest.json`, em `recovery_data[installation_id]`, registra `backup_path`,
`backup_fingerprint`, `backup_source_path` e `backup_run_id`. A resolução de banco registra também
os endpoints, schemas, origem remota e URLs. Para inspecionar uma interrupção:

1. abra o manifesto do run e confirme `failed_step` e `last_successful_step`;
2. localize `backup_path` e compare/revalide `backup_fingerprint` antes de qualquer restauração;
3. preserve tanto o backup quanto a cópia parcial de TESTE durante a investigação;
4. use `resume` somente se a impressão da cópia atual ainda coincidir com o manifesto;
5. para restauração manual, copie o snapshot para um staging, valide-o e só então faça a troca —
   não torne o backup gravável nem o edite no local.

Em instalações aninhadas, o backup do pai contém a árvore original inteira antes da substituição e
fica fora do document root; portanto não é apagado quando `htdocs` é recriado. Os filhos são
transferidos por seus próprios steps conforme as exclusões determinísticas do plano.

A conexão de PRODUÇÃO é efêmera: `DB_USER` e `DB_PASSWORD` não entram em logs, exceções,
`repr()`, manifestos, state ou recovery data, nem em argumentos de subprocessos. O cliente MySQL
recebe as credenciais em `--defaults-extra-file` temporário com permissão `0600`, removido em
sucesso e erro. O snapshot de conexão guarda apenas metadados não secretos. Antes de retomar a cópia do banco,
`resume` relê o `wp-config.php`, redescobre a conexão e exige banco, host e porta iguais ao snapshot;
usuário e senha podem mudar e não são comparados nem persistidos no estado.

## Widgets

`snapshot` persiste `widget_snapshot` no manifesto antes dos checkpoints de Core.
A comparação protege bytes e `autoload` de `widget_*`, `sidebars_widgets`, `template` e
`stylesheet`, em todas as tabelas do schema cujo nome termina em `_options` (não filtra pelo
prefixo da instalação); não é uma validação visual do site. Diferenças ficam em `widget_diff` e fazem
`widget_validation` falhar sem restauração implícita.

```bash
wp-modernizer --config config.yaml resume example-site --run-id "<run-id>" --restore-widgets
```

A opção autoriza restauração apenas quando `widget_validation` encontra diferenças e ainda
será executada. Não é um comando de restauração imediata. Ela deve ser repetida explicitamente
em cada invocação de `resume`; uma autorização anterior não é herdada. O runtime restaura via
MySQL e relê as opções para verificar o resultado. Em dry-run não restaura widgets.

O formato e os caminhos do state estão em [observabilidade](observability.md).
