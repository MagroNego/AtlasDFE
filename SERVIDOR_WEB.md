# Implantação do servidor

## Processo e rede

Execute **um processo com um worker**, usando banco preferencialmente em disco local. Travas, fila de exportação e tarefas são locais ao processo; múltiplas instâncias não coordenam o consumo SEFAZ. O lançador e `python -m nfe_consulta.web.app` iniciam um worker.

O endereço padrão é `127.0.0.1:8080`. Para acesso interno, use HTTPS por reverse proxy e limite o acesso de rede aos usuários autorizados. A conta do serviço precisa ler certificado/segredos e gravar banco e pastas operacionais. Configure inicialização e reinício pelo gerenciador de serviços do sistema operacional; o pacote não instala automaticamente um serviço.

`GET /healthz` retorna estado básico e versão, sem verificar disponibilidade do banco fiscal. Teste também acesso e Status após implantação.

## Configuração do processo

`WEB_CONFIG.example` não é carregado automaticamente. Defina variáveis no lançador/ambiente do serviço e reinicie após alterações.

| Variável | Padrão/uso |
| --- | --- |
| `NFE_WEB_ENV` | `production` no lançador Windows |
| `NFE_WEB_HOST` / `NFE_WEB_PORT` | `127.0.0.1` / `8080` |
| `NFE_WEB_ROOT_PATH` | Prefixo da aplicação quando publicada em subcaminho pelo proxy |
| `NFE_WEB_FORWARDED_ALLOW_IPS` | `127.0.0.1`; somente proxies confiáveis |
| `NFE_DATABASE_PATH` | Caminho alternativo ao padrão; `secrets/db-path.txt` salvo pelo Admin prevalece |
| `NFE_WEB_CORPORATE_AUTH` | `0`: desativado; `1`: exige gateway autenticado, segredo e grupos (seção abaixo) |
| `NFE_WEB_LOCAL_AUTH` | `0`: consultas livres e login somente Admin; `1`: modo legado com contas locais para todos |
| `NFE_ADMIN_ACCOUNTS_PATH` | Banco de contas; padrão `dados/admin_accounts.db` |
| `NFE_ADMIN_SESSION_MINUTES` | Inatividade: 30 minutos; faixa aceita 5–480 |
| `NFE_AUTO_SYNC_ENABLED` | `1` no lançador; `0` quando app iniciado diretamente sem configuração |
| `NFE_AUTO_SYNC_WEEKDAYS` | `0,1,2,3,4,5,6`; segunda=0, domingo=6 |
| `NFE_AUTO_SYNC_MAX_LOTES` | 50; faixa 1–500 |
| `NFE_RECOVERY_ENABLED` | `0`; recuperação pontual pausada |
| `NFE_WEB_AUDIT_LOG` | Padrão `logs/web_audit.log` |

A janela padrão é **22:00 ≤ horário ou horário < 04:00**, Brasília (UTC−3). Início e fim são configuráveis no Admin e valem para chamadas manuais, automáticas e retentativas. Disparos padrão às **22h, 23h, 00h, 01h, 02h e 03h**, configuráveis no Admin junto com a janela. A rotina considera os dias configurados e os bloqueios persistidos. Leitura local não depende dessa janela.

`NFE_AUTO_SYNC_ONCE_DATE`, `NFE_AUTO_SYNC_INTERVAL_HOURS`, `NFE_AUTO_SYNC_HOUR`, `NFE_AUTO_SYNC_MINUTE` e `NFE_SEFAZ_COOLDOWN_MINUTES` não substituem o agendamento atual nem o cooldown fixo de 60 minutos. Remova configurações legadas. Detalhes: [Distribuição e Qive](DISTRIBUICAO_DFE_QIVE.md).

## Autenticação e segredos

No modo padrão (`NFE_WEB_LOCAL_AUTH=0`, `NFE_WEB_CORPORATE_AUTH=0`), consultas e relatórios não exigem login. Admin, importações, configurações e alterações manuais da situação de notas exigem uma sessão administrativa válida. A identidade pública nunca recebe privilégios administrativos, inclusive em requisições diretas. Cadastro público e gerenciamento de usuários ficam desativados nesse modo.

Execute `CONFIGURAR_ADMIN.cmd` para criar ou redefinir a conta administrativa antes do primeiro acesso. Não há senha padrão; sem uma conta configurada, o Admin permanece bloqueado e o acesso comum continua disponível. O configurador salva hash PBKDF2-SHA256 com salt no banco `dados/admin_accounts.db`, preservando dados fiscais e outras contas. Contas comuns antigas não permitem login no modo padrão. Senhas são de pelo menos 12 caracteres. A sessão tem expiração por inatividade (30 minutos por padrão), limite absoluto de oito horas e revogação no logout/redefinição da senha. Login possui limites de tentativas por conta e endereço.

O modo legado com contas para todos pode ser habilitado explicitamente com `NFE_WEB_LOCAL_AUTH=1` e reinício. O modo corporativo continua separado e exige configuração explícita do gateway. `NFE_WEB_AUTH_MODE=proxy` sozinho não autentica ninguém. CSRF, limites de upload, trava e janela SEFAZ permanecem ativos. A auditoria usa `usuario-interno` para acesso comum e o nome da conta para ações administrativas.

Use HTTPS no acesso de rede para proteger a senha e o cookie durante o transporte. O cookie tem HttpOnly e SameSite=Strict; Secure é ativado quando a requisição é HTTPS. HTTP local permanece disponível, portanto a proteção do transporte depende da implantação. Não exponha diretamente o servidor à internet.

`INICIAR_WEB.cmd` gera `secrets/web-csrf-secret.txt` quando ausente. Em outros lançadores, forneça esse arquivo ou `NFE_WEB_CSRF_SECRET`, com pelo menos 32 caracteres aleatórios. Preserve o segredo entre reinícios.

| Arquivo padrão | Conteúdo |
| --- | --- |
| `secrets/auto-sync.json` | Estado, janela, horários e dias da sincronização automática salvos pelo Admin |
| `secrets/db-path.txt` | Banco fiscal selecionado |
| `secrets/db-password.txt` | Senha SQLCipher |
| `secrets/cert-path.txt` | Caminho do PFX/P12 |
| `secrets/cert-password.txt` | Senha do certificado |
| `secrets/web-csrf-secret.txt` | Segredo Web persistente |
| `secrets/smtp-password.txt` | Credencial SMTP |
| `secrets/smtp-sender.json` | Remetente e usuário salvos pela interface |
| `secrets/email-recipients.json` | Destinatários cadastrados |

A senha do Admin fica no banco de contas como hash, tanto no modo padrão quanto no modo de contas para todos. O arquivo legado `secrets/admin-password.txt`, quando presente, pode inicializar a conta; use `CONFIGURAR_ADMIN.cmd` para configurar a senha sem manter esse arquivo em texto. `NFE_DATABASE_PASSWORD`, `NFE_CERT_PASSWORD` e `NFE_ADMIN_PASSWORD` não são fontes de senha. Restrinja permissões NTFS/Unix das pastas operacionais.

## Certificado e SEFAZ

Configure PFX/P12 em Admin; a aplicação referencia o arquivo original. Para Windows Certificate Store, use `CurrentUser` ou `LocalMachine`. Sob conta de serviço, conceda leitura da chave privada à identidade que executa o processo.

```text
NFE_CERT_STORE=LocalMachine
NFE_CERT_THUMBPRINT=<impressão digital do certificado>
```

A agenda salva em `secrets/auto-sync.json` prevalece sobre `NFE_AUTO_SYNC_ENABLED` e `NFE_AUTO_SYNC_WEEKDAYS`. O Admin aplica as mudanças sem reiniciar o processo. Desativar não aborta uma sincronização ativa nem desabilita a função manual. Arquivo inválido impede a inicialização com mensagem de configuração, sem retornar silenciosamente aos padrões.

O cliente atual usa PowerShell e o endpoint de produção:

```text
https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx
SOAPAction: http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe/nfeDistDFeInteresse
```

O endpoint consta em `nfe_consulta/distribuicao.py`; não há configuração do Gateway Qive na interface. Linux é documentado para uso local/desenvolvimento, não como sincronização real já validada.

## SMTP

```text
NFE_SMTP_HOST=<servidor>
NFE_SMTP_PORT=587
NFE_SMTP_SECURITY=starttls
NFE_SMTP_FROM=<remetente autorizado>
NFE_SMTP_USER=<usuário SMTP>
```

Use `ssl` quando o provedor exigir TLS direto, com a porta correspondente. A senha fica em `secrets/smtp-password.txt`. Remetente/usuário em `secrets/smtp-sender.json` prevalecem sobre o ambiente. Cadastre destinatários pela interface.

Para Gmail com senha de app, remetente/usuário devem corresponder à conta autorizada dessa credencial. Trocar o endereço sem ajustar a autenticação não configura outra conta de envio. O botão de teste valida a aceitação SMTP; a entrega deve ser conferida na caixa destinatária.

A fila `notificacoes_email` persiste no banco fiscal. Somente `210240` gera novos alertas. Reenvio da fila não consulta a SEFAZ. Aceitação SMTP e confirmação no banco não são atômicas; uma interrupção pode duplicar mensagem.

## Exportações, logs e continuidade

- Geração: duas tarefas simultâneas, até 16 em espera, limite de cinco minutos de espera por vaga (não é limite da duração da geração). Fila cheia retorna 429; espera expirada, 503; `Retry-After: 15`.
- Interface solicita geração em segundo plano com HTTP 202 e consulta estado a cada dois segundos. Estados/tarefas em andamento não persistem após reiniciar.
- Cancelamento mantém a vaga até encerrar o trabalho; verifica SQL e linhas. A gravação final do Excel pode terminar antes da limpeza.
- Arquivos prontos ficam em `dados/downloads` por 24 horas. Downloads prontos não entram na fila de geração.
- Auditoria: `logs/web_audit.log` e rotação. Ações de sincronização são descartadas após 30 dias; a rotação por tamanho pode removê-las antes. Arquivos de até 5 MiB, com cinco cópias; demais ações seguem essa rotação.
- Backup completo é manual. Consulte [Banco e backup](SEGURANCA_BANCO.md).

## Preparação para login corporativo

O modo corporativo integra o app a um gateway de autenticação externo ao AtlasDFE. O gateway realiza SSO com o provedor corporativo (por exemplo, Entra ID via OIDC); o AtlasDFE valida a credencial do gateway e aplica autorização por grupos. Não há cliente OIDC/SAML, tela de login corporativo ou validação de tokens do provedor dentro do app.

O modo fica **desativado por padrão**. Para habilitar, configure no ambiente do processo e reinicie:

```text
NFE_WEB_LOCAL_AUTH=0
NFE_WEB_CORPORATE_AUTH=1
NFE_WEB_AUTH_MODE=proxy
NFE_WEB_ALLOWED_GROUPS=<identificador do grupo Consulta>
NFE_WEB_ADMIN_GROUPS=<identificador do grupo Administrador>
```

Gere um segredo aleatório de pelo menos 32 caracteres, diferente do segredo CSRF, e salve em `secrets/web-proxy-secret.txt`. Configure o mesmo valor somente no gateway. Alternativamente, use `NFE_WEB_PROXY_SECRET` no ambiente do serviço. Não inclua esse segredo no navegador, JavaScript, repositório ou logs. Ausência de segredo, grupos ou configuração compatível impede a inicialização; não há retorno automático ao acesso aberto.

Contrato de cada requisição encaminhada pelo gateway:

| Cabeçalho padrão | Conteúdo |
| --- | --- |
| `X-NFE-Proxy-Secret` | Segredo compartilhado entre gateway e app |
| `X-NFE-User` | Identificador corporativo estável e único, como UPN/e-mail; até 254 caracteres, normalizado para minúsculas |
| `X-NFE-Name` | Nome para exibição; opcional |
| `X-NFE-Groups` | Identificadores dos grupos autorizados, separados por vírgula; até 16 KiB |

Os nomes podem ser definidos com `NFE_WEB_PROXY_SECRET_HEADER`, `NFE_WEB_USER_HEADER`, `NFE_WEB_NAME_HEADER` e `NFE_WEB_GROUPS_HEADER`; devem ser distintos. Grupos são comparados por igualdade, sem distinção de maiúsculas. Use IDs estáveis do diretório e configure o gateway para enviá-los integralmente, inclusive quando o provedor exigir resolução adicional de grupos. `NFE_WEB_ADMIN_USERS` e opções `DEV_*` não concedem permissões neste modo.

| Associação | Acesso |
| --- | --- |
| Grupo de consulta | Consulta, Status, Emissão, relatórios e Downloads |
| Grupo administrativo | Acesso de consulta, importação, sincronização e configurações |
| Nenhum grupo autorizado ou identidade/segredo inválido | HTTP 401 |
| Consulta tentando função administrativa | HTTP 403 |

O backend deve ficar inacessível aos clientes, preferencialmente em `127.0.0.1`, atrás de HTTPS. O gateway deve remover e sobrescrever **todos** os cabeçalhos acima recebidos do navegador, preencher identidade/grupos exclusivamente após autenticação e inserir o segredo apenas na conexão interna. Entre máquinas, proteja também o enlace gateway–app com TLS e firewall. O segredo autentica o gateway; o app confia na identidade que ele atesta.

Cadastro no provedor, URL pública/callback, certificados TLS, grupos, MFA, expiração e logout são configurações do gateway. O AtlasDFE não cria usuários nem armazena senhas corporativas. Mudanças/revogação de grupos passam a valer quando o gateway atualizar sua sessão; tarefas de exportação já aceitas podem concluir. Não há botão de logout local nesse modo. A auditoria passa a identificar o operador pelo identificador corporativo. CSRF permanece obrigatório e vinculado à identidade e aos grupos. Relatórios locais continuam compartilhados conforme as regras atuais; SSO não cria segregação fiscal por usuário.

`/healthz` e recursos estáticos permanecem públicos no backend; o gateway pode protegê-los adicionalmente. Rotas de cadastro e contas locais continuam desativadas. Em operação normal, o gateway trata o redirecionamento ao login antes de encaminhar requisições ao app.

### Teste da integração

1. Em homologação, iniciar com agenda desativada na configuração efetiva e sem envio SMTP real.
2. Confirmar que acesso sem gateway ou com cabeçalhos forjados recebe 401.
3. Validar uma conta de consulta: leitura/exportação permitidas; Admin, importação e POST direto de configuração recusados.
4. Validar uma conta administrativa e confirmar CSRF, janela SEFAZ e auditoria individual.
5. Remover a associação/autorização no provedor, renovar a sessão do gateway e confirmar a recusa. Validar logout e MFA conforme a política corporativa.

Referência para o contrato de um gateway: [integração oficial do OAuth2 Proxy](https://oauth2-proxy.github.io/oauth2-proxy/7.11.x/configuration/integration/). Configure o gateway para enviar exatamente os cabeçalhos da tabela acima; o AtlasDFE não instala nem configura esse serviço.
