# Admin-only login implementation plan

> Histórico: a exigência de senha no modo padrão foi removida na versão 2.7.3 a pedido do usuário. Os manuais da raiz descrevem o acesso atual.

> Execução nesta sessão, com testes antes da alteração e revisão final.

**Goal:** acesso fiscal sem senha, autenticação obrigatória para Admin e cancelamento manual.
**Architecture:** `WebSettings.admin_only_auth` deriva do modo não corporativo sem contas completas. `current_user` retorna identidade pública sem privilégios ou sessão administrativa válida. Middleware e dependências distinguem acesso fiscal permitido de autenticação administrativa.
**Tech Stack:** Python, FastAPI, SQLite, Jinja2, pytest.
**Spec:** docs/superpowers/specs/2026-11-03-admin-only-login-design.md

## Constraints
- Sem senha padrão, novas dependências de produção ou alteração dos dados fiscais.
- Preservar modos legados e corporativo explicitamente habilitados.
- Logout, inatividade, revogação e CSRF continuam efetivos.

## Review focus
- Cookies inválidos ou sessões antigas comuns não concedem Admin.
- Sem conta configurada, Administração falha fechada; consultas funcionam.
- POST direto não burla permissão ou CSRF.
- Não remover nem alterar contas antigas comuns silenciosamente.
- Mostrar navegação/relatórios sem sessão, sem formular ações administrativas ao visitante.

## Task 1: Fronteira de acesso e login
- [x] Escrever testes de acesso público, bloqueio de todas as mutações e login/logout admin no modo padrão; executar e observar falhas.
- [x] Modificar settings.py, auth.py e middleware/login em app.py; preservar testes de modo legado/corporativo.
- [x] Verificar cookie falso, sessão comum antiga, inatividade, ausência de conta e cancelamento manual em ambas as ações.

## Task 2: Interface, configuração e entrega
- [x] Adaptar base.html/login.html e desativar cadastro/gerenciamento de usuários no modo padrão; manter interface legada.
- [x] Atualizar instruções de configuração e testes do modo de rede com o novo contrato.
- [x] Executar pytest completo, testes JavaScript e diff --check. Revisar diff e abrir PR com validação; integrar após CI se autorizado.

## Verificação e decisões
- RED: três testes demonstraram Admin liberado sem sessão antes da correção.
- Suíte completa final: 466 testes passaram (49,21 s); quatro arquivos JavaScript passaram; compileall e git diff --check passaram. Revisão independente não encontrou falhas importantes. Avisos de depreciação já existentes nas dependências foram reportados pelo pytest.
- Decisão: gerenciamento de contas permanece desativado no modo padrão; CONFIGURAR_ADMIN.cmd configura a conta. Contas antigas são preservadas.
- Modo legado LOCAL_AUTH=1 continua exigindo contas para todos; padrão LOCAL_AUTH=0 implementa o requisito confirmado.
