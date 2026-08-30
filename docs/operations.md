# Operações

- `inventory` coleta campos independentes e marca valores indisponíveis em vez de interromper.
- `diagnose` informa capacidades tipadas e integridade.
- `plan` apresenta exclusões de cópia entre pai/filho, estado da resolução do banco de dados,
  etapas, pontos de controle e trabalho pendente que considera a serialização, sem alterar estado.
- `migrate` cria uma cópia de segurança ou substitui apenas com autorização explícita e prepara
  arquivos e banco de dados.
- `update` executa etapas independentes com pontos de controle.
- `pipeline` concatena migração e atualização e é o fluxo operacional normal.
- `resume` carrega uma execução, compara a impressão digital da instalação e só prossegue se ela
  for consistente.

`--dry-run`, no nível global ou do comando, impede que qualquer etapa mutável invoque seu
adaptador. Uma simulação ainda sonda capacidades e grava seu manifesto externo de execução, para
que o trabalho proposto possa ser auditado. Intencionalmente, não existe comando de publicação
em produção.

O adaptador público de execução é conservador: a migração real permanece desabilitada até que a
implantação forneça e teste por contrato a origem SSH, a descoberta do banco de origem, a
retenção de cópias de segurança e o gravador protegido de `wp-config`. Ele falha antes de alterar
o estado quando a infraestrutura não está definida.
