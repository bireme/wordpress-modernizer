# ADR-008: Política versionada de modernização e roteamento PHP

## Contexto

Instalações WordPress antigas não devem saltar indiscriminadamente para a versão atual. A rota
depende da versão inicial, da compatibilidade PHP de cada versão intermediária e dos binários
disponíveis no servidor operacional de TESTE. A retomada também não pode adotar silenciosamente
uma política publicada depois do início do run.

## Decisão

O domínio contém uma `ModernizationPolicy` declarativa, identificada por `policy_id` e
`policy_revision`. A revisão inicial classifica instalações como `ANCIENT` (antes de 4.9),
`LEGACY` (4.9 a 6.8, inclusive) ou `CURRENT` (acima de 6.8). Ela declara os checkpoints 5.3 e 6.2
com PHP 7.4, e 6.8 com PHP 8.1 a 8.4 conforme a matriz oficial atualmente auditada. O destino
final é `latest` sob o runtime `current` configurado.

O planner resolve cada requisito contra `php_runtimes` e grava a seleção dentro de cada
`PlannedStep`. `OperationStep` conserva esse valor e `RuntimeOperations` entrega o caminho PHP ao
adapter WP-CLI. A execução resultante é `<php absoluto> <wp absoluto> ...`; não há alteração do
PHP padrão, FPM ou servidor web.

`plan` lê a versão do arquivo `wp-includes/version.php` por uma porta restrita, verifica cada
binário com `<php> -v` e pode consultar `/etc/os-release` e o cache APT para produzir orientação.
Essas operações são somente leitura. Instalação, `sudo`, adição de repositórios e alteração de
`update-alternatives` não fazem parte de nenhuma porta operacional.

A rota, sua identidade, requisitos e seleções são serializados no manifesto. `resume` executa os
`planned_steps` persistidos e reinspeciona o próximo runtime; ele não consulta a política atual.

## Consequências

Uma nova rota é introduzida alterando dados da política e, se necessário, a matriz auditada do
resolvedor. Uma futura automação `ANCIENT` poderá declarar PHP 5.6/7.2 e checkpoints adicionais
sem mudar o runner ou o contrato de `OperationStep`. Enquanto isso, versões anteriores a 4.9 são
bloqueadas antes de mutação e recebem orientação para uma ponte manual.
