# Arquitetura

O pacote segue a arquitetura de portas e adaptadores com camadas pragmáticas. `domain` contém
modelos imutáveis, enums, invariantes, análise de caminhos, nomenclatura e planejamento, sem
importar APIs de processos externos. `application` contém os casos de uso e as portas `Protocol`.
`pipeline` contém etapas independentes e o executor que preserva o estado em caso de falha.
`infrastructure` fornece adaptadores de subprocessos, estado local, YAML/ambiente, MySQL,
SSH/rsync por chave, SSH/SFTP por senha, WP-CLI, sistema de arquivos e Git. `cli` trata apenas da
composição.

A composition root em `cli.main.build_service` liga a configuração ao
`EnvironmentSecretProvider`, cria os adaptadores SSH/MySQL/WP-CLI, injeta um roteador de
transporte que escolhe chave ou senha explicitamente em `RuntimeOperations` pelas portas da
aplicação e, por fim, constrói `ModernizerService`. O mesmo roteador implementa
`SourceInspectionPort`, uma porta separada e semanticamente restrita à inspeção do
`wp-config.php`: ela retorna apenas `DB_NAME`, `DB_HOST` e `$table_prefix` validados e não oferece
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
