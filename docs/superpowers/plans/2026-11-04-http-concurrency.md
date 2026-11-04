# Terceira etapa: respostas HTTP e concorrência

Objetivo aprovado: medir páginas completas e navegação durante operações em segundo plano, depois de otimizar consultas SQL.

## Escopo

- Medir resposta HTTP real com templates, middleware e auditoria, em bancos temporários de 10 mil/cem mil notas, sequencialmente e com quatro requisições simultâneas.
- Medir SQLCipher separadamente, incluindo a leitura do estado do banco antes da consulta. Não confundir os tempos HTTP com navegador/rede da empresa.
- Tentar a navegação em navegador local e registrar o ambiente ou impedimento concreto.
- Exercitar consulta durante exportação XLSX e transação SQLite protegida pela trava de sincronização, sem chamadas reais à SEFAZ.
- Corrigir apenas bloqueios demonstrados por testes. Manter a tentativa de gravação de auditoria aguardada antes da resposta, sem descarte intencional de logs ou fila em memória. Isso não acrescenta garantia contra falha de disco ou encerramento do processo.

## Diagnóstico confirmado

As operações pesadas já usam threads e as leituras não aguardam a trava da sincronização. Testes com exportador pausado e transação de escrita real passaram. A gravação síncrona de auditoria nos handlers assíncronos bloqueou o loop HTTP quando o stream do log foi suspenso; a resposta `/healthz` não conseguia terminar até liberar a escrita.

## Implementação

Manter `AuditLog.write` síncrono para seus chamadores em threads. Nas rotas HTTP assíncronas de `web/app.py` e `web/xml_routes.py`, aguardar `run_in_threadpool(app.state.audit.write, ...)`. Isso mantém a mesma tentativa de gravação antes da resposta, permitindo que o loop atenda outras requisições durante I/O de log. Não alterar regras de autorização, sincronização, exportação ou banco.

A medição adicional em SQLCipher com dez mil notas encontrou ~386 ms por Consulta, com ~172 ms na leitura de disponibilidade e ~201 ms na consulta. Ambas abrem o mesmo banco separadamente. Incluir nesta etapa uma conexão única por pedido de Consulta, criada/usada/fechada no mesmo worker, compartilhada entre verificação e leitura. Sem conexão persistente, cache fiscal ou mudança na preparação de esquema. Emissão continua separada e seu custo residual será registrado.

## Verificação

- [x] Reproduzir bloqueio de auditoria: teste falhou antes da alteração; testes positivos de exportação/escrita passaram.
- [x] Rodar os testes determinísticos após a correção e a suíte completa.
- [x] Demonstrar duas aberturas antes da correção e uma depois; verificar fechamento, banco ausente/legado, erro e atualização dos resultados no pedido seguinte.
- [x] Registrar medições HTTP e resultado da tentativa de navegador.
- [x] Revisar diff, resultados e limites; preparar atualização do PR #15 sem integrar na main.

Agentes divididos entre medição HTTP/navegador e concorrência. Integração e revisão sob coordenação principal. Os testes de concorrência usam eventos de coordenação, não uma carga real de produção nem transmissão à SEFAZ.

Verificação final: 484 testes Python passaram (avisos de depreciação do runtime, sem falhas) e cinco testes JavaScript passaram. Revisão independente sem achados importantes/críticos pendentes. Casos de senha vazia/inacessível e rotação entre pedidos foram acrescentados após a revisão. Dados: `docs/benchmarks/2026-11-04-web-response.json`; SQLCipher dez mil notas: primeira página de ~382 para ~210 ms e página seguinte de ~380 para ~212 ms, somente resposta da aplicação. Navegador bloqueado pelo ambiente (socket/ptrace EPERM), portanto não foi medido clique até pintura.
