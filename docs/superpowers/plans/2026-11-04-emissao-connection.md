# Quarta etapa: conexão da Emissão

Objetivo: retirar aberturas repetidas de SQLite/SQLCipher da listagem de Emissão e dos detalhes, preservando filtros, totais e navegação.

## Diagnóstico e desenho

A listagem `/xml` abre o banco para `query_report` e novamente para as CC-e. O detalhe de itens abre para a nota e para os itens. O detalhe de manifestações abre para a nota, verifica a tabela de eventos em outra conexão e abre novamente para ler os eventos. A medição anterior apontou aproximadamente 375 ms na listagem com dez mil notas em SQLCipher; cada abertura adicional repete a derivação da chave.

Separar as consultas que recebem uma conexão emprestada das funções públicas que abrem/fecham o banco. Criar `web/emissao_data.py` para coordenar cada pedido com uma conexão somente leitura, criada/usada/fechada no mesmo worker. Senha relida uma vez por pedido. Sem cache fiscal, conexão persistente, novos índices ou alteração do SQL.

Listagem mantém vazio para banco ausente, mensagens de filtro/configuração e notas já lidas quando falha somente a consulta de CC-e. Detalhes mantêm 404 de nota inexistente antes dos filtros adicionais. Arquivo de senha vazio/inacessível nos detalhes passa a receber erro genérico 400, compatível com o tratamento existente de senha inválida, em vez de erro interno 500.

## Execução

- [x] Ler fluxos e preservar cópia da revisão anterior para medição.
- [x] Demonstrar contagens anteriores de conexão e testes de fechamento/thread/senha/filtros/erro antes de implementar.
- [x] Extrair funções emprestadas e coordenar listagem, itens e manifestações em conexão única.
- [x] Comparar respostas completas antes/depois, em dados temporários e processos separados.
- [x] Executar suíte completa e revisão independente; preparar publicação no mesmo PR #15, sem integrar na main.

Referência pública: `d872efa7ef1340ddd425d1e36837cf0152abde91`, com a mesma árvore `82659773bf30e905fbb6aa70997c4d9873687694` do commit local `2fb36f7`. Metas: reduzir aberturas, não prometer latência constante. Os testes ASGI não medem rede nem pintura do navegador; não repetir a tentativa de Chromium já bloqueada por EPERM.

Evidência: seis casos de contagem de conexão falharam antes da implementação, mostrando duas/duas/três aberturas na listagem/itens/manifestações. Após a correção, 93 testes focados passaram; verificação final com 512 testes Python e cinco JavaScript passou, assim como compileall e diff-check. Revisão independente sem achados importantes/críticos pendentes; prioridade de validação corrigida durante a revisão. Comparação de HTML completo e fontes antes/depois passou, com uma abertura por pedido em todos os cenários medidos. Dados e custos em `docs/benchmarks/2026-11-04-emissao-response.json` e `docs/desempenho-consultas.md`.
