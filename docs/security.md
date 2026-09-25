# Modelo de segurança

As fronteiras de segurança incluem destinos somente de TESTE, caminhos canônicos e endpoints em
listas de permissão, chaves de host SSH estritas por padrão, adapters separados para autenticação
por chave e senha, execução de subprocessos por `argv` sem shell, limites de tempo, segredos
apenas no provedor, ocultação centralizada de saída/`argv` e estado externo de execução. O adapter
SSH por senha entrega a credencial diretamente à API Paramiko em memória; não usa `sshpass`,
`expect`, variável de subprocesso ou linha de comando para credenciais. O comando remoto de
cópia usa GNU tar, com caminhos escapados para o shell e enviados como bytes. A extração local
valida caminhos e links, usa descritores com `O_NOFOLLOW`, recusa arquivos especiais e não
aplica owner/group ou bits especiais. Stderr remoto é drenado sem ser registrado.
Dumps e arquivos compactados são ignorados pelo Git; isso não é uma garantia de
exclusão universal da transferência. O plano inclui `*.sql` e `.wp-modernizer`, além
das instalações filhas. Criptografia e retenção dependem da implantação.

Antes da publicação, execute a varredura de segredos/topologia documentada em [desenvolvimento](development.md),
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

Links simbólicos absolutos são preservados pelo transporte por senha. A extração não os
segue para escrever, mas acessos posteriores podem alcançar seus destinos; não se deve
interpretar a validação de extração como isolamento completo da aplicação copiada.

As credenciais efêmeras de conexão são protegidas nos adapters, mas os artefatos não são
livres de dados sensíveis: `wp-config.php` copiado/backup contém credenciais e o snapshot de
widgets guarda bytes das opções no manifesto, que também pode aparecer nos logs. O state
store não sanitiza genericamente o conteúdo do site nem o criptografa. Restrinja o acesso
a state, logs e backups; não publique esses artefatos. A sanitização do logger trata chaves,
padrões e secrets conhecidos, não garante remover qualquer segredo embutido em conteúdo.

Limite confirmado da sanitização atual: a saída isolada de `wp config get DB_PASSWORD`
pode ser gravada sem máscara em `command_result.stdout`. O logger não identifica a
sensibilidade da saída pelo comando, e a composition root não registra automaticamente
os valores do `EnvironmentSecretProvider` no `Redactor`. Isso afeta as verificações locais
da conexão de TESTE; logs operacionais devem ser tratados como potencialmente portadores
de credenciais. É uma lacuna de implementação, não uma garantia de proteção oferecida.
