# Modelo de segurança

As fronteiras de segurança incluem destinos somente de TESTE, caminhos canônicos e endpoints em
listas de permissão, chaves de host SSH estritas por padrão, adapters separados para autenticação
por chave e senha, execução de subprocessos por `argv` sem shell, limites de tempo, segredos
apenas no provedor, ocultação centralizada de saída/`argv` e estado externo de execução. O adapter
SSH por senha entrega a credencial diretamente à API Paramiko em memória; não usa `sshpass`,
`expect`, variável de subprocesso ou linha de comando para credenciais. O comando remoto de
cópia usa GNU tar, com caminhos escapados para o shell e enviados como bytes. A extração local
valida caminhos e links, usa descritores com `O_NOFOLLOW`, recusa arquivos especiais e não
aplica owner/group ou bits especiais. Stderr remoto é drenado sem ser registrado. Dumps de bancos de
dados e arquivos compactados são ignorados e exigem criptografia/retenção no nível da implantação.

Antes da publicação, execute a varredura de segredos/topologia documentada em `development.md`,
inspecione cada ocorrência, escolha uma licença e revise o histórico do Git.

Na cópia SSH/tar por senha, exclusões são aplicadas também na origem quando a conversão
preserva a semântica do filtro local: caminhos literais (inclusive nomes non-UTF8) e
padrões ASCII sem `/` que usam somente `*`, como `*.sql`. Nomes simples, como
`.wp-modernizer`, valem em qualquer profundidade; caminhos com `/` são relativos à
raiz copiada após normalização. A raiz em si não é excluída. Argumentos são escapados
para o shell, metacaracteres literais são escapados para GNU tar e o comando é enviado
como bytes sob `LC_ALL=C`.

Globs com `?`, classes `[...]`, barras, barras invertidas ou caracteres não ASCII
permanecem somente no filtro local, assim como `.` e padrões contendo NUL. Esses
conteúdos ainda podem atravessar o stream SSH, mas não são extraídos quando excluídos
pelo filtro local. A restrição evita diferenças entre o casamento de caracteres do
Python e o casamento de bytes do GNU tar. Todas as exclusões, inclusive as aplicadas
remotamente, continuam sendo verificadas na extração local como segunda barreira.
