# Recuperação e preservação em caso de falha

Nenhuma atualização que falha provoca reversão. O executor registra em estado externo a última
etapa bem-sucedida, a etapa que falhou, a integridade antes/depois, detalhes fatais, pontos de
controle, diferenças de widgets, operações pendentes e uma impressão digital do sistema de
arquivos. Investigue a cópia de TESTE preservada, corrija-a manualmente e execute `resume`. Uma
impressão digital alterada é informada como intervenção manual e exige revisão; ela nunca é
aceita silenciosamente.

Para abandonar uma execução, inicie uma nova migração com `--replace-existing`. Uma cópia de
TESTE existente deve ser salva com sucesso antes que o adaptador destrutivo possa removê-la. As
proteções de analisador, lista de permissão, caminho exato, ambiente e link simbólico devem ser
aprovadas. Caminhos de produção não passam por essa proteção.
