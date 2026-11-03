# Arquitetura técnica

## Componentes

| Módulo | Responsabilidade |
| --- | --- |
| `web/app.py` | Rotas, middleware, autorização e coordenação |
| `web/auth.py`, `web/admin_accounts.py` | Acesso público, login administrativo, sessões, permissões e CSRF |
| `web/consulta_local.py`, `consulta_numeros.py` | Consultas locais, filtros e correspondência por números de notas |
| `web/scheduler.py`, `web/auto_sync_config.py`, `web/sync_runtime.py` | Agendamento, trava, cooldown e configuração |
| `janela_consulta.py` | Restrição de horário antes das chamadas externas |
| `sincronizacao.py`, `distribuicao.py` | Fluxo distNSU e transporte SOAP/PowerShell |
| `recuperacao_nsu.py` | Recuperação pontual e limite de consultas |
| `banco.py`, `seguranca_banco.py` | Transações e SQLite/SQLCipher |
| `xml_reader.py`, `web/xml_store.py`, `web/xml_routes.py` | Extração, importação e relatórios de emissão |
| `cancelamento.py`, `cte.py` | Situação local e vínculos de CT-e |
| `web/export_queue.py`, `web/export_jobs.py`, `export_control.py` | Fila, execução assíncrona e cancelamento cooperativo |
| `web/downloads.py` | Arquivo temporário de relatórios |
| `web/email_alerts.py` | Configuração SMTP e processamento de notificações |
| `web/audit.py`, `web/sync_history.py` | Auditoria, diagnóstico e retenção operacional |

Os caminhos da tabela são relativos a `nfe_consulta/`. O CNPJ e a UF usados na distribuição estão definidos em `config.py`; não há gerenciamento multiempresa na interface.

## Persistência e consistência

O banco fiscal mantém manifestações, dados das notas, cancelamentos, CT-e, estado de distribuição, cooldown, intervalos, agendamento e fila SMTP. `xml_documentos`, `xml_relatorio` e `xml_importacoes` mantêm metadados, hash, dados extraídos e registros de importação. Colunas XML legadas permanecem por compatibilidade; documentos novos não mantêm cópias permanentes do original.

Respostas `distNSU` são persistidas antes do parser. A confirmação de processamento, os dados e o cursor são transacionais. Resposta inválida permanece pendente e impede avanço silencioso. Rejeição 656 preserva cursor e registra pausa/divergência; `nsu_para_consulta()` retorna o cursor local gravado. Respostas `consNSU` também são preservadas durante o processamento e limpas após resultado confirmado.

Importação usa a trava compartilhada com sincronização e não altera cursor. Cancelamentos homologados e marcações administrativas alimentam a situação local; remoção de marcação manual não desfaz evento da SEFAZ. CT-e é armazenado em `eventos_cte`, separado de manifestações. A ausência de vínculo não prova inexistência de conhecimento.

Por padrão, consultas e relatórios usam a identidade pública `usuario-interno`, sem permissão administrativa. `current_user` reconhece a sessão de Admin pelo banco de contas. Admin, importações e alterações manuais de situação exigem autenticação e autorização no servidor. Cadastro e gerenciamento de contas pela Web respondem 404 nesse modo; a conta administrativa é configurada por `CONFIGURAR_ADMIN.cmd`. CSRF continua validado no servidor. O modo legado de contas está disponível somente com `NFE_WEB_LOCAL_AUTH=1`; nesse caso, preserva PBKDF2, sessões e perfis existentes.

O modo corporativo opcional (`NFE_WEB_CORPORATE_AUTH=1`) exige um gateway autenticado com segredo compartilhado e grupos autorizados. `current_user` valida os cabeçalhos atestados pelo gateway; a autorização distingue Consulta e Administrador. Configuração incompleta impede iniciar o app e requisições sem identidade autorizada recebem 401. O app não implementa OIDC/SAML diretamente. Contrato e implantação: [Servidor Web](SERVIDOR_WEB.md#preparação-para-login-corporativo).

## Concorrência e agendamento

A implantação exige um processo/worker. As travas locais não coordenam processos separados. O banco deve permanecer preferencialmente em disco local.

Janela externa padrão: **22:00 inclusive a 04:00 exclusiva, UTC−3**. Disparos padrão: **22, 23, 00, 01, 02 e 03 horas**. A agenda persistida em `secrets/auto-sync.json` substitui os padrões de ativação, janela, dias e horários. A janela também é aplicada às chamadas manuais e relida entre chamadas externas. O loop permanece aguardando quando desativado e é acordado por evento após salvar no Admin; uma execução ativa termina com a trava reservada. Horários usam data civil de Brasília, ficam dentro da janela e são separados por pelo menos 60 minutos.

O controle em `controle_agendamento_intervalo` evita repetir oportunidade concluída após reinício. Cooldown e pausas persistem no banco. A oportunidade mais recente elegível pode ser retomada; o agendador não reproduz todas as oportunidades perdidas.

`consNSU` inicia desabilitado. Se habilitado com lacunas, a recuperação tem prioridade e consome a execução antes de nova distribuição. Política completa: [Distribuição DF-e e Qive](DISTRIBUICAO_DFE_QIVE.md).

## Relatórios e cancelamento

Trabalho pesado executa em threads; duas exportações simultâneas e 16 aguardando. A interface recebe HTTP 202 e acompanha o estado em memória. Relatórios prontos ficam no filesystem por 24 horas.

O pedido de cancelamento sinaliza um `threading.Event`, propagado por `ContextVar`. Consultas SQL usam progress handler; montagem das linhas verifica o sinal. O worker encerra antes de liberar a vaga. A serialização final do Excel ainda conclui o bloco corrente; o resultado cancelado é descartado. Arquivos publicados não são removidos por cancelamento de geração. Interromper a transferência no navegador preserva a cópia pronta.

## Limites de validação

Importação verifica estrutura mínima e consistência de campos; não valida assinatura XML nem autorização atual na SEFAZ. A situação Autorizada é a classificação local quando não há cancelamento registrado. O app não transmite manifestações ou cancelamentos.

A CI executa testes sintéticos em Windows/Linux. Os testes automatizados não fazem chamadas reais à SEFAZ nem enviam e-mails. Os resultados da operação estão registrados em [Distribuição e Qive](DISTRIBUICAO_DFE_QIVE.md#evidência-dos-testes-noturnos). Aceitação SMTP e confirmação no banco não formam transação única; pode ocorrer duplicidade após interrupção.

O empacotador usa lista explícita de arquivos, sem dados/segredos. A publicação requer CI aprovada e cria ZIP/checksum somente quando a versão ainda não possui release. Código novo na main não substitui ZIP já publicado com a mesma versão.
