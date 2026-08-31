# Modelo de segurança

As fronteiras de segurança incluem destinos somente de TESTE, caminhos canônicos e endpoints em
listas de permissão, chaves de host SSH estritas por padrão, adapters separados para autenticação
por chave e senha, execução de subprocessos por `argv` sem shell, limites de tempo, segredos
apenas no provedor, ocultação centralizada de saída/`argv` e estado externo de execução. O adapter
SFTP entrega a senha diretamente à API Paramiko em memória; não usa `sshpass`, shell, `expect`,
variável de subprocesso ou linha de comando. Dumps de bancos de
dados e arquivos compactados são ignorados e exigem criptografia/retenção no nível da implantação.

Antes da publicação, execute a varredura de segredos/topologia documentada em `development.md`,
inspecione cada ocorrência, escolha uma licença e revise o histórico do Git.
