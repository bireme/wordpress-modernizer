# Política de segurança

Não abra uma issue pública que contenha credenciais, topologia interna, dumps de banco de dados
ou configuração do WordPress. Relate suspeitas de vulnerabilidade pelo canal privado de
segurança da organização.

Segredos devem ficar em variáveis de ambiente ou em uma futura implementação de
`SecretProvider`; eles nunca devem ser versionados.

State, logs e backups também podem conter dados sensíveis da instalação, inclusive opções
de widgets e arquivos de configuração. A sanitização de logs não substitui controle de
acesso, retenção e criptografia externos. Consulte o [modelo de segurança](docs/security.md).
