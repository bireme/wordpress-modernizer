# Observabilidade

## Logs do CLI

`src/wp_modernizer/observability/` contém `__init__.py` e `logging.py`.
`StructuredLogger.event` adiciona `timestamp` UTC e `event`, sanitiza recursivamente
os campos com `Redactor` e grava JSON pelo módulo `logging`, no nível INFO.
Somente esses dois campos são automáticos; os demais dependem do chamador.

`diagnose`, `migrate`, `update`, `pipeline` e `resume` criam um arquivo por invocação,
inclusive em dry-run:

```text
<state_directory>/logs/<data>_<hora-com-microssegundos>_<operação>_<instalação>.log
<state_directory>/logs/<data>_<hora-com-microssegundos>_resume_<instalação>_<run-id-origem>.log
```

Os nomes usam horário local e componentes normalizados; cada linha contém um objeto JSON
(JSON Lines). O CLI informa `log_path` na saída JSON e o caminho no resumo de terminal.
`inventory` e `plan` não criam esses arquivos. `migrate`, `update` e `pipeline` toleram
`OSError` na criação do log e podem continuar com log indisponível; `diagnose` e `resume`
não têm esse fallback. Uma falha anterior à criação do manifesto pode deixar apenas o log.

## Eventos e campos

Além de `timestamp` e `event`, o reporter estruturado emite:

| Evento | Campos |
|---|---|
| `run_started` | `installation`, `operation`, `dry_run`, `run_id`, `total_steps` |
| `capability_result` | `stage`, `capability`, `available`, `detail`, `health` |
| `capability_fatal_error` | `stage`, `error` |
| `step_started` | `step`, `index`, `total` |
| `step_finished` | `step`, `index`, `total`, `status`, `changed`, `message`, `metrics`, `installation` |
| `run_finished` | `manifest` |
| `run_failed` | `reason`, `manifest` |
| `command_result` | `argv`, `cwd`, `environment`, `return_code`, `stdout`, `stderr`, `elapsed_seconds`, `correlation_id` |

Há variantes no CLI: `diagnose` inicia com apenas `installation`/`operation`, emite
`capability_result` com `capability`, `available` e `detail`, e termina com `report`.
Erros capturados pelo CLI emitem `run_failed` com apenas `reason`.
`ObservedCommandRunner` emite `command_result` somente quando o delegate retorna e há
logger ativo. `environment` contém o mapa passado à chamada, não o ambiente completo do
processo. Operações Paramiko diretas não passam por esse wrapper.

Não há injeção automática de `run_id`, instalação, ambiente operacional ou servidor em
cada evento. Use o arquivo da invocação, o evento inicial, o manifesto e, quando fornecido,
`correlation_id` para correlação. Não há duração de execução/etapa obrigatória em todos os eventos;
comandos têm `elapsed_seconds` e algumas etapas acrescentam métricas específicas.

## Manifestos e checkpoints

O `JsonStateStore` persiste separadamente:

```text
<state_directory>/<installation-id>/runs/<run-id>/manifest.json
<state_directory>/<installation-id>/runs/<run-id>/checkpoints/0000-<etapa>.json
```

O manifesto contém plano, resultados, parâmetros, snapshots e `recovery_data`.
Cada checkpoint contém `step` e `health`; a numeração cresce dentro do run.
São JSON formatados, gravados por arquivo temporário seguido de substituição.
Os diretórios `snapshots/` e `logs/` dentro do run são criados pelo store, mas os logs
operacionais do CLI ficam no `logs/` global acima. O snapshot de widgets fica no manifesto,
com bytes em hexadecimal; planos Multisite/HTTPS/indexação são strings JSON em `recovery_data`.
Esses dados não são criptografados pelo store e podem conter conteúdo sensível do site.
Consulte [segurança](security.md) e [recuperação](recovery.md).

## Métricas e configurações sem integração operacional

`Metrics` é um acumulador em memória com `add` e `snapshot`. Não está ligado à composição
operacional nem coleta automaticamente contadores de runs, WP-CLI ou bytes migrados.
Os dicionários `StepResult.metrics`, por exemplo `replacements`, `sites_changed` e métricas
de widgets, são independentes desse acumulador e aparecem nos resultados persistidos.

`ObservabilityConfig` aceita `json_stdout`, `log_file` e `otel_enabled`, mas a composition
root não os consome: não mudam destino dos logs, formato do terminal nem ativam telemetria.
Use `--json` para a saída estruturada do CLI; ela não é um stream de eventos do logger.

O extra `.[otel]` declara dependências OpenTelemetry, mas não existe exporter OTLP nem
inicialização SDK integrada ao fluxo. Instalar o extra ou definir `OTEL_*` não exporta
logs, traces ou métricas. Collector e endpoint OTLP não são requisitos operacionais atuais.

Há uma lacuna conhecida: `command_result.stdout` pode conter sem máscara a saída isolada
de `wp config get DB_PASSWORD`. A sanitização por padrões não identifica todo retorno de
comando sensível. Veja o [limite de segurança dos logs](security.md).
