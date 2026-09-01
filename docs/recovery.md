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
