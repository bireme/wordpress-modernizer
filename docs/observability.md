# Observabilidade

Por padrão, a operação emite eventos JSON estruturados e persiste relatórios JSON por execução
sem depender de serviço externo. Os campos de correlação obrigatórios são `run_id`,
`installation_id`, operação, etapa, ambiente, ID do servidor, estado e duração. A interface
interna de métricas oferece contadores de execução/etapa, integridade, WP-CLI, widgets, descoberta
de bancos de dados e bytes migrados.

OpenTelemetry é opcional por meio do extra `otel`. Uma implantação pode encaminhar eventos e
métricas internas para OTLP usando as variáveis de ambiente padrão `OTEL_*`. A ausência de um
Collector nunca impede a execução. As credenciais do exportador devem ser referências a segredos
e são ocultadas.
