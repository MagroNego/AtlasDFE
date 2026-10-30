# Janela configurável e melhorias — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Aplicar a proposta aprovada sem alterar main nem dados de produção.
**Architecture:** Política de janela imutável com provedor ContextVar por execução/requisição; configuração persistente compartilhada com a agenda. Otimizações locais preservam resultados fiscais.
**Tech Stack:** Python, FastAPI, SQLite/SQLCipher, Jinja, pytest.
**Spec:** docs/superpowers/specs/2026-10-30-janela-configuravel-design.md

## Global Constraints

- Padrão 22:00–04:00, Brasília; início inclusivo/fim exclusivo.
- Até seis disparos, intervalo mínimo de 60 minutos; dias civis selecionados.
- Sem alterações de cursor ou consulta externa ao salvar; main intacta.

## Review Focus

- Janela diurna e noturna com minutos e borda exclusiva: testes de política.
- Alteração entre lotes: teste de provedor vivo.
- Legado JSON e gravação inválida: testes de persistência.
- Multipart sem Content-Length: teste ASGI de bytes reais.
- Duplicada com cancelamento: manter regressões existentes de cancelamento.

## Task 1: Política e agenda

**Files:** janela_consulta.py, web/auto_sync_config.py, web/settings.py; tests/test_configurable_window.py.
**Interfaces:** JanelaConsulta(inicio, fim), janela_atual(), usar_janela(provider), proxima_abertura(instante=None); validate_schedule(..., window_start='22:00', window_end='04:00').
- [x] Escrever testes de bordas, atualização do provedor, persistência e dias civis.
- [x] Executar testes, implementar e verificar novamente.

## Task 2: Integração Admin/scheduler

**Files:** web/app.py, web/scheduler.py, web/sync_runtime.py, templates, diagnósticos.
**Interfaces:** mesmos métodos públicos; campos sync_window_start/sync_window_end em settings.
- [x] Testar salvamento admin, CSRF e mudança sem reinício.
- [x] Propagar provedor vivo e mensagens dinâmicas; manter cooldown e recuperação.

## Task 3: Limites e desempenho

**Files:** web/xml_upload_limit.py, banco.py, web/status_view.py, web/xml_store.py, web/xml_routes.py.
- [x] Testes de limite de corpo sem Content-Length e regressões de relatórios/cancelamento.
- [x] Aplicar limites por rota e eliminar consultas/cópias redundantes.
- [x] Validar suíte, diff e revisão independente antes de publicar apenas a branch.

## Registro

Execução autorizada pelo usuário em 30/10/2026 ('Pode aplicar'); não solicitar nova confirmação intermediária. Mudanças de exportação que exijam alterar layout ficam fora desta etapa; reduzir primeiro cópias e consultas.

Verificação final: 422 testes passaram; revisão independente e correção de retentativas concluídas. Ruling: preservado Workbook/layout; escrita write_only adiada por exigir verificação visual específica. Sem alegação de memória constante.
