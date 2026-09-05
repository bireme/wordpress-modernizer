# Recuperação e preservação em caso de falha

Nenhuma atualização que falha provoca reversão. O executor registra em estado externo a última
etapa bem-sucedida, a etapa que falhou, a integridade antes/depois, detalhes fatais, pontos de
controle, diferenças de widgets, operações pendentes e uma impressão digital do sistema de
arquivos. Investigue a cópia de TESTE preservada, corrija-a manualmente e execute `resume`. Uma
impressão digital alterada é informada como intervenção manual e exige revisão; ela nunca é
aceita silenciosamente.

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
sucesso e erro. O estado guarda apenas metadados não secretos. Antes de retomar a cópia do banco,
`resume` relê o `wp-config.php`, redescobre a conexão e exige banco, host e porta iguais ao snapshot;
usuário e senha podem mudar e não são comparados nem persistidos no estado.
