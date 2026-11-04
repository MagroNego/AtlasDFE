# Login somente para administradores

> Histórico: a exigência de senha no modo padrão foi removida na versão 2.7.3 a pedido do usuário. Os manuais da raiz descrevem o acesso atual.

Requisito confirmado: usuários comuns acessam o Atlas sem senha; Admin e alteração manual de nota para cancelada exigem autenticação administrativa.

Abordagem: reutilizar contas, hash PBKDF2, limites de tentativas e sessões existentes. No modo padrão `NFE_WEB_LOCAL_AUTH=0`, oferecer acesso fiscal sem sessão e login reservado a administradores. Remover a identidade de rede com privilégios administrativos. Preservar os modos explicitamente configurados de contas locais completas e gateway corporativo.

Sem conta configurada, consultas continuam disponíveis e ações administrativas ficam bloqueadas. `CONFIGURAR_ADMIN.cmd` cria/redefine a conta sem senha padrão. Cadastro público desativado no modo padrão; contas antigas comuns preservadas mas não aceitas como login administrativo. Importações e demais mutações administrativas mantêm a exigência de Admin. CSRF e expiração/revogação de sessão continuam ativos.

Alternativas: exigir conta de todo usuário contraria o requisito; SSO exige infraestrutura não disponível. Acesso compartilhado sem privilégios + sessão administrativa resolve a necessidade usando os mecanismos existentes.
