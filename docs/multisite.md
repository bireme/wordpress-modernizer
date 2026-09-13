# P1.1 — Domínios Multisite na cópia de TESTE

A migração corrige redes WordPress por subdiretórios ou subdomínios. Instalações comuns
passam pelas etapas condicionais sem mudanças adicionais. A detecção lê definições literais
no `wp-config.php`, sem depender da heurística textual do diagnóstico ou executar PHP copiado.

## Ordem e persistência

Por instalação, inclusive instalações aninhadas:

1. `backup_existing_test` e `copy_files`.
2. `snapshot_source_database`: resolve conexão, URL de origem, URL de TESTE e prefixo real.
3. `copy_database` e `write_test_db_config`.
4. `plan_multisite_domain`: inspeciona a cópia no endpoint de TESTE, verifica a conexão local,
   compara a topologia com as constantes e produz o plano exato de valores anteriores/finais.
5. O runner grava o plano em `RunManifest.recovery_data[installation_id].multisite_plan`.
6. `correct_multisite_domain`: aplica o plano, grava `DOMAIN_CURRENT_SITE` atomicamente,
   compara os dados finais e enumera a rede com WP-CLI.
7. Por instalação: `pending_search_replace`, `plan_test_https` e `enforce_test_https`
   ([P1.2](test-https.md)), seguidos da modernização quando a operação é `pipeline`.

A resolução de URLs já ocorria no snapshot anterior à importação; não foi duplicada.
O runner adia suas sondagens de bootstrap entre a cópia e a correção estrutural.
As duas novas etapas ficam apenas **planejadas** em dry-run: a cópia/importação não é executada,
portanto não é possível comprovar a transformação sem a cópia real de TESTE.
O search-replace mantém o dry-run nativo existente quando seus requisitos estão disponíveis.

O plano usa o campo de recuperação já serializado pelo state store. Não há novo formato de
manifesto obrigatório. Um resume preserva o plano e pula etapas concluídas. Uma correção
interrompida aceita, em cada campo, somente o valor original ou o final registrado; qualquer
terceiro valor, mudança de IDs, inclusão/remoção de sites ou mudança de paths bloqueia a retomada.
Não há dependência de rollback transacional do MySQL para retomar uma aplicação parcial.
Manifests antigos sem o plano não autorizam uma correção estrutural improvisada.

## Responsabilidades

- `domain/multisite.py`: snapshots tipados, transformação e validação de topologia/URLs.
- `WordPressConfigWriter`: inspeção conservadora e autorização separada para alterar somente
  `DOMAIN_CURRENT_SITE`. Preserva permissões, escrita atômica e inode quando já está correto.
- `DatabasePort`/`MySQLAdapter`: lê `{prefix}site`, `{prefix}blogs` e as tabelas options de cada
  blog. Atualiza somente `domain` e os valores exatos de `home`/`siteurl`, com condições sobre
  chave e valor anterior. Não altera IDs, relações, paths, autoload ou outros options.
- `RuntimeOperations`: coordena as portas, exige TESTE e verifica que o wp-config corresponde
  à conexão de TESTE antes de permitir bootstrap. Não há SQL nem manipulação de arquivo no service.
- `PipelineRunner`: persiste o planejamento antes da mutação e preserva a cópia nas falhas.

`home`/`siteurl` precisam conter URLs HTTP(S) simples coerentes com cada blog. Por isso podem
ser atualizados por igualdade exata sem manipular dados serializados. Os demais conteúdos
continuam com o [search-replace preciso do WP-CLI](https://developer.wordpress.org/cli/commands/search-replace/).
O search-replace Multisite usa as URLs originais persistidas, mesmo quando `siteurl` já foi corrigido.
Não há SQL `REPLACE()` nem transformação global de hostnames estruturais.

A validação compara configuração, domínios, paths, IDs, relações e options com o plano e exige
que [wp site list](https://developer.wordpress.org/cli/commands/site/list/) enumere exatamente
os blogs esperados, usando explicitamente a URL de TESTE. Uma leitura SQL adicional após
esse bootstrap confirma que o estado permaneceu correto.

## Limites deliberados: falhar e preservar

São recusados, sem adivinhar valores:

- Constantes ausentes, duplicadas, condicionais ou não literais; IDs/path inconsistentes.
- Múltiplas redes na mesma instalação, domínios mapeados externos, colisões entre sites ou
  mistura ambígua de domínios de origem e destino antes do planejamento.
- Tabelas estruturais/options ausentes ou options obrigatórios duplicados/serializados.
- Mudanças de esquema HTTP/HTTPS, portas ou path da raiz; home/siteurl com paths distintos da
  topologia; hostnames fora do formato DNS ASCII conservador.
- Roteamento especial por `SUNRISE`, `WP_HOME`, `WP_SITEURL`, `WP_CONTENT_DIR` ou
  `WP_CONTENT_URL`; drop-ins `db.php`, `object-cache.php` ou `advanced-cache.php`.
  Esses recursos podem compartilhar banco/cache com PRODUÇÃO e exigem isolamento específico.
  A correção não executa um flush de cache potencialmente compartilhado.
- Destino igual à origem ou com sobreposição de URL/hostname que torne o search-replace ambíguo.

O suporte não configura DNS/vhosts/certificados dos novos subdomínios. A conversão HTTPS interna é responsabilidade do [P1.2](test-https.md). Não implementa
bloqueio de indexação, traduções ou postflight geral.

Os testes locais exercitam planejamento, adapters com comandos simulados, escrita real de
wp-config e persistência real do manifesto. A integração externa do projeto continua dependente
de fixtures de um laboratório descartável; não se deve interpretar esses testes como uma
migração real de WordPress/MySQL executada automaticamente.
