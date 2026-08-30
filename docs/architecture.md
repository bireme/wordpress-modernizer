# Arquitetura

O pacote segue a arquitetura de portas e adaptadores com camadas pragmáticas. `domain` contém
modelos imutáveis, enums, invariantes, análise de caminhos, nomenclatura e planejamento, sem
importar APIs de processos externos. `application` contém os casos de uso e as portas `Protocol`.
`pipeline` contém etapas independentes e o executor que preserva o estado em caso de falha.
`infrastructure` fornece adaptadores de subprocessos, estado local, YAML/ambiente, MySQL,
SSH/rsync, WP-CLI, sistema de arquivos e Git. `cli` trata apenas da composição.

As dependências apontam para dentro. Objetos falsos implementam os mesmos `Protocol`s e permitem
testar todas as regras de segurança sem WordPress. Dataclasses modelam valores estáveis do
domínio; Pydantic valida configurações não confiáveis na fronteira. Isso evita acoplar o domínio
a questões de serialização.

A sondagem de capacidades avança da camada 0 (arquivos/PHP/configuração/banco de dados/arquivos
do núcleo), passando pela presença e pré-inicialização do WP-CLI e pela inicialização reduzida,
até a inicialização normal. Uma camada inferior continua útil quando uma superior falha.
