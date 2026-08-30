# Requisitos de implantação ainda necessários

| Pergunta | Motivo | Formato esperado | Impacto se ausente |
|---|---|---|---|
| Quais são as raízes permitidas das aplicações? | delimitar caminhos destrutivos | lista YAML de caminhos absolutos | substituição desabilitada |
| Quais servidores de origem e impressões digitais SSH estão aprovados? | conexão e autenticidade do host | IDs de servidor, nomes DNS, portas e implantação de `known_hosts` | migrações indisponíveis |
| Qual provedor de segredos será usado em produção? | obter credenciais sem arquivos | nome/configuração do provedor ou política de variáveis de ambiente | apenas provedor de ambiente |
| Quais endpoints de bancos de teste são permitidos? | descoberta determinística de bancos | IDs de endpoint/DNS/portas e referências a segredos | localizador informa que não encontrou |
| Bancos de teste ausentes podem ser criados? Por quem? | a criação é privilegiada/destrutiva | booleano por endpoint e processo de concessão | nunca são criados automaticamente |
| Quais estratégias de nomes/URLs são necessárias? | convenções específicas de cada instalação | estratégia nomeada e exemplos | substituições explícitas obrigatórias |
| Qual política de proprietário/grupo/modo do sistema de arquivos se aplica? | permissões seguras após a cópia | UID/GID/modo ou adaptador de implantação | nenhuma alteração de proprietário |
| Quais pontos de controle do núcleo são aceitos por site? | compatibilidade controlada de atualização | lista ordenada de versões | apenas etapas genéricas configuradas são executadas |
| Quais plugins gerenciados e qual política para árvore suja se aplicam? | evitar perder trabalho local | repositório público/acessível, branch e política | atualização gerenciada ignorada |
| Onde o estado externo é mantido e copiado? | durabilidade da retomada e auditoria | diretório absoluto e política de retenção/criptografia | apenas estado local configurado |
| É necessária compatibilidade com senha no SSH? | escolha de adaptador/segurança | sim/não e mecanismo de transporte seguro | apenas autenticação por chave |
| Qual destino de telemetria e política de dados estão aprovados? | exportação OTLP opcional | endpoint, referências de ambiente para TLS/autenticação e retenção | apenas logs JSON locais |
