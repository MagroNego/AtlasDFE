# Segunda etapa: contagem com CC-e agrupada

Objetivo: reduzir o trabalho da contagem agrupada sem mudar a seleção, a apresentação ou a paginação das notas.

A consulta anterior conta linhas depois de excluir cartas com manifestação e selecionar uma carta por nota órfã. A nova contagem agrega por chave: soma a quantidade de eventos diferentes de CC-e quando existir pelo menos um; caso contrário, conta uma linha. Todos os filtros são aplicados antes da agregação. A consulta que escolhe os IDs da página fica preservada.

Alternativa avaliada: substituir também a seleção dos IDs por agregação. A experiência sintética reduziu a contagem de ~96 para ~32 ms, mas piorou a seleção da página de ~173 para ~327 ms; por isso, esta etapa altera apenas a contagem.

- [x] Investigar custo e comparar alternativas em base temporária de cem mil notas.
- [x] Adicionar teste de plano com uma única leitura da fonte e casos com múltiplas manifestações, cartas sem manifestação e filtros de data/código/número.
- [x] Trocar somente a contagem quando `agrupar_cartas=True`.
- [x] Comparar resultados completos e tempos antes/depois no mesmo banco, com amostras alternadas.
- [x] Executar testes focados, suíte completa e revisão antes de publicar.

Sem dependências, índices, cache de resultado ou regras fiscais novos. A contagem continua proporcional ao conjunto filtrado; não há promessa de tempo constante.

Validação: 41 testes focados passaram; suíte completa com 466 testes Python passou (449 avisos de depreciação já existentes) e 5 testes JavaScript passaram. Teste de plano falhou antes da implementação (três buscas na fonte em vez de uma). Revisão independente sem achados importantes/críticos. Benchmark completo e comparação de resultados em `docs/benchmarks/2026-11-04-contagem-agrupada.json`; resumo em `docs/desempenho-consultas.md`.
