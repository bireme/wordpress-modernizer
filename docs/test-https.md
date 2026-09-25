# HTTPS interno na cópia de TESTE

A migração normaliza referências HTTP dos hosts próprios da cópia para HTTPS. Não configura
certificados, DNS, vhosts ou proxy: o domínio de TESTE deve oferecer HTTPS.
A proteção de [indexação](test-indexing.md) é outra etapa, executada depois de HTTPS;
as traduções pertencem à atualização. Não há uma etapa separada de postflight geral.

## Ordem e arquitetura

O planejamento mantém a preparação de todas as instalações (inclusive aninhadas):
`backup_existing_test` → `copy_files` → `snapshot_source_database` → `copy_database` →
`write_test_db_config` → `plan_multisite_domain` → `correct_multisite_domain`.

Depois, para cada instalação: `pending_search_replace` (quando pendente) → `plan_test_https`
→ `enforce_test_https` → `plan_test_indexing` → `disable_test_indexing`.
Na operação `pipeline`, todas essas etapas precedem modernização,
updates de core/plugins/temas, traduções e validações existentes. O comando independente
`update` não inclui essas etapas; HTTPS integra `migrate` e `pipeline`.

O domínio contém a transformação e as regras de replay; `application/test_https.py` coordena
`DatabasePort`, `WordPressPort` e `WordPressConfigWriterPort`. O runtime exige destino TESTE;
a etapa verifica também o ambiente do endpoint, a conexão completa do wp-config e o prefixo
persistido antes do bootstrap. Nenhum acesso a PRODUÇÃO é necessário para a etapa HTTPS.

O módulo [Multisite](multisite.md) é responsável pela migração estrutural dos domínios. HTTPS não
escreve wp-config, IDs, paths nem hostnames estruturais. `DOMAIN_CURRENT_SITE` permanece um
hostname sem protocolo, e as demais constantes Multisite permanecem inalteradas.

## Escopo das substituições

Single-site: o hostname exato da URL de TESTE resolvida no snapshot. `home` e `siteurl` devem
pertencer a esse host; seus paths podem ser diferentes e são preservados.

Multisite: somente os hosts enumerados na topologia corrigida e validada pelo módulo Multisite. Redes por
subdiretórios compartilham uma transformação de host; redes por subdomínios têm uma por host.
Os blogs afetados ficam registrados no plano. Não se infere uma lista ilimitada de subdomínios.

Usamos [WP-CLI search-replace](https://developer.wordpress.org/cli/commands/search-replace/)
com `--all-tables-with-prefix`, `--precise`, `--regex`, `--regex-flags=i` e, no Multisite,
`--network`. A expressão exige um delimitador de URL ou fim de string após o hostname.
Isso evita transformar hosts externos como `teste.example.org.external` ou userinfo como
`teste.example.org@external`. Não há substituição global de `http://` nem SQL `REPLACE()`.

A cobertura inclui posts, páginas, postmeta, options, widgets, menus, metadata e tabelas de
plugins/temas com o prefixo da instalação, incluindo prefixos customizados. O motor do WP-CLI
preserva a serialização PHP, inclusive valores aninhados, ao transformar URLs em HTML,
CSS inline e configurações. URLs externas HTTP ficam inalteradas e não causam falha.

## Persistência, retomada e validação

`RunManifest.recovery_data[installation_id].test_https_plan` guarda versão, endpoint, banco,
prefixo, configuração da rede, snapshots anterior/final, pares HTTP/HTTPS, expressões e IDs
dos blogs afetados. O runner salva o plano antes da primeira mutação. Não se reutiliza o plano
nem a marca de conclusão de `pending_search_replace` para esta transformação distinta.
A pendência anterior só recebe conclusão global quando todas as instalações planejadas
concluem seu próprio search-replace, com marcas de recuperação por instalação.
As duas novas etapas ficam apenas planejadas em dry-run, pois dependem da cópia preparada.

Resume usa esse plano e aceita, em cada `home`/`siteurl`, apenas o valor anterior ou final.
Mudanças de topologia, configuração ou conexão falham fechadas. Antes de cada transformação,
um search-replace em dry-run determina se restam ocorrências; um host já concluído não recebe
outra escrita. A mesma lógica permite retomar uma interrupção dentro de um search-replace,
ou após apenas parte dos sites/options ter mudado. Sem plano persistido não há aplicação.

Ao final, leituras explícitas do banco comparam `home`/`siteurl` e topologia com o snapshot
HTTPS esperado; no Multisite, `wp site list` precisa enumerar os mesmos IDs, relações,
domínios e paths. Novos dry-runs devem retornar zero referências HTTP internas substituíveis.
Uma última inspeção confirma que configuração e estrutura permaneceram corretas.
`test_https_validated` registra a conclusão; o StepResult registra a contagem de substituições.
Uma execução sem ocorrências informa `changed=false`, inclusive quando o conteúdo já era HTTPS.

## Limitações e fail-closed

- `WP_HOME`, `WP_SITEURL`, `WP_CONTENT_URL`, `WP_CONTENT_DIR`, `SUNRISE` e drop-ins de
  banco/cache são recusados, também em single-site. Não há edição textual genérica de PHP.
- Valem os limites estruturais do módulo Multisite, incluindo redes múltiplas e domínios mapeados externos.
- URLs estruturais com portas, credenciais, query/fragment ou hostname não conservador são
  recusadas. Referências de conteúdo com autoridade contendo porta explícita não pertencem
  aos pares sem porta deste plano e são preservadas.
- URLs codificadas (por exemplo `http:\/\/`, percent-encoding, base64), arquivos de temas/plugins
  e recursos fora das tabelas com o prefixo não são decodificados/editados por esta etapa.
- A cobertura de serialização segue o motor do WP-CLI, não substitui chaves de arrays nem
  decodifica formatos arbitrários. O adapter recusa código de saída não zero ou contagem
  inválida; stderr isolado com sucesso e contagem válida não causa falha nessa operação.
  Portanto, a presença de avisos deve ser investigada nos logs.
- Regex pode ser mais lento que o search-replace literal; mantém-se o timeout do adapter.
  Falhas preservam TESTE para retomada. Não há rollback automático ou flush de cache remoto.

Os testes cobrem ports/adapters, persistência real do manifesto e aplicação parcial simulada.
Um contrato local adicional executa o motor real de serialização do WP-CLI em PHP, sem
bootstrap de WordPress, banco ou rede; ele é pulado quando PHP/WP-CLI phar não está disponível.
A integração WordPress/MySQL completa continua dependendo de laboratório opt-in.
