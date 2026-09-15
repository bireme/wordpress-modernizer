# Arquitetura

A modernização de versões é uma política declarativa e versionada do domínio. Ela não conhece
subprocessos: recebe uma versão WordPress, runtimes já inspecionados e um destino resolvido, e
produz uma rota imutável. Portas separadas inspecionam a versão da origem, descobrem PHP, resolvem
o WordPress final e geram orientação de provisionamento. Os adapters implementam leitura remota
restrita, `<php> -v`, destino configurado e consulta somente leitura ao sistema operacional.

O caminho do dado executável é `ModernizationPolicy -> ModernizerService -> PlannedStep ->
OperationStep -> RuntimeOperations -> WPCLIAdapter`. O runtime e o destino WordPress não são
reinferidos nos adapters. O mesmo plano segue para o `RunManifest` e para o state; `resume` lê os
steps originais e não recalcula a política. Consulte
[ADR-008](adrs/ADR-008-versioned-modernization-policy.md).

O pacote segue a arquitetura de portas e adaptadores com camadas pragmáticas. `domain` contém
modelos imutáveis, enums, invariantes, análise de caminhos, nomenclatura e planejamento, sem
importar APIs de processos externos. `application` contém os casos de uso e as portas `Protocol`.
`pipeline` contém etapas independentes e o executor que preserva o estado em caso de falha.
`infrastructure` fornece adaptadores de subprocessos, estado local, YAML/ambiente, MySQL,
SSH/rsync por chave, SSH/tar por senha (SFTP para inspeção), WP-CLI, sistema de arquivos e Git. `cli` trata apenas da
composição.

A composition root em `cli.main.build_service` liga a configuração ao
`EnvironmentSecretProvider`, cria os adaptadores SSH/MySQL/WP-CLI, injeta um roteador de
transporte que escolhe chave ou senha explicitamente em `RuntimeOperations` pelas portas da
aplicação e, por fim, constrói `ModernizerService`. O mesmo roteador implementa
`SourceInspectionPort`, uma porta separada e semanticamente restrita à inspeção do
`wp-config.php` (a inspeção de versão usa também `wp-includes/version.php`): ela retorna
`DB_NAME`, `DB_HOST`, `DB_USER`, `DB_PASSWORD` e `$table_prefix` validados e não oferece
execução arbitrária ou operações de atualização. A URL `siteurl` vem de uma leitura fixa na porta
MySQL. O filesystem implementa o contrato de criação e verificação de backup
imutável; o runtime só libera a substituição depois de receber essa evidência.

As dependências apontam para dentro. Objetos falsos implementam os mesmos `Protocol`s e permitem
testar todas as regras de segurança sem WordPress. Dataclasses modelam valores estáveis do
domínio; Pydantic valida configurações não confiáveis na fronteira. Isso evita acoplar o domínio
a questões de serialização.

A sondagem de capacidades avança da camada 0 (arquivos/PHP/configuração/banco de dados/arquivos
do núcleo), passando pela presença e pré-inicialização do WP-CLI e pela inicialização reduzida,
até a inicialização normal. Uma camada inferior continua útil quando uma superior falha.
Essas capacidades WP-CLI/PHP são locais ao servidor operacional/TESTE; o servidor WordPress de
PRODUÇÃO não executa WP-CLI, PHP CLI nem bootstrap remoto.

O domínio é inferido individualmente de `source_path`, relativamente à raiz de
`allowed_app_roots`: `/home/apps/example.org/wp-example/htdocs` sob `/home/apps` identifica
`example.org`. Instalações diferentes podem pertencer a domínios diferentes. `destination_path`
é opcional: omitido, usa o mesmo caminho de `source_path` no servidor operacional de TESTE.
Um caminho explícito continua permitido como exceção.

A conexão de PRODUÇÃO é efêmera: `DB_USER` e `DB_PASSWORD` não entram em logs, exceções,
`repr()`, manifestos, state ou recovery data, nem em argumentos de subprocessos. O cliente MySQL
recebe as credenciais em `--defaults-extra-file` temporário com permissão `0600`, removido em
sucesso e erro. O snapshot de conexão guarda apenas metadados não secretos. Antes de retomar a cópia do banco,
`resume` relê o `wp-config.php`, redescobre a conexão e exige banco, host e porta iguais ao snapshot;
usuário e senha podem mudar e não são comparados nem persistidos no estado.

`DatabaseConfig` é exclusivo de TESTE. A origem usa `SourceDatabaseConnection` em memória,
sem consultar o cadastro de endpoints de TESTE.

## Nomes Unix legados na transferência por senha

`PasswordSFTPAdapter` mantém o nome público e o contrato `FileTransferPort`, mas a cópia
usa `LC_ALL=C tar --format=gnu --hard-dereference` em um canal SSH autenticado pelo Paramiko.
`listdir_attr()` decodifica nomes como UTF-8 dentro do protocolo, antes de entregá-los ao
adapter; aplicar `surrogateescape` depois desse ponto não resolve. O stream tar recebe os
bytes originais: `tarfile` os decodifica com UTF-8 + `surrogateescape` desde a leitura do header,
e o filesystem Unix os reconstitui sem transcoding ou alteração da origem.

O rsync por chave já transporta nomes sem conversão de charset (não usamos `--iconv`).
Reutilizar seu subprocesso para senha exigiria outro mecanismo de autenticação; por isso
router e portas continuam compartilhados, mas os transportes permanecem separados.

A extração é manual, sem `extractall`: valida raiz e traversal, recusa hardlinks no archive,
arquivos especiais e escapes/ciclos de links relativos. Links absolutos são preservados
como links, sem seguir seu destino durante a extração. Links relativos são validados
como um grafo antes da criação. Caminhos locais nunca são percorridos através de symlinks.
Arquivos são publicados por substituição de temporários privados; diretórios recebem metadados
ao final. Owner/group não são aplicados; modos recebem `u+w,g+w`, como no rsync, removendo bits
especiais. O destino deve estar sob controle da conta operacional, sem mutadores concorrentes.

O prazo total cobre leitura, extração e espera do status remoto; um timer fecha o canal se o
servidor não responder ao pedido de execução. Stdout e stderr são drenados antes do status;
stderr e mensagens brutas de bibliotecas não são propagados. Erros usam categorias seguras e,
quando há um stream válido, o código de saída remoto.

Limitações: GNU tar deve existir na origem; o destino requer filesystem Unix com suporte a
`dir_fd`/`O_NOFOLLOW`. Excludes são sempre aplicados localmente e, quando preservam
a semântica, também na origem; veja [segurança](security.md). Somente os filtros
exclusivamente locais deixam o conteúdo excluído trafegar e exigem sua leitura.
Hardlinks remotos viram cópias regulares;
ACLs, xattrs e atime não são preservados. Falhas podem deixar uma árvore parcial, como antes;
não há staging global ou mudança na autorização de mutações, que continua restrita a TESTE.

Referências: [tarfile e encoding](https://docs.python.org/3/library/tarfile.html),
[Paramiko Channel e drenagem antes do status](https://docs.paramiko.org/en/stable/api/channel.html).
