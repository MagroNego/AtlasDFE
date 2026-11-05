# Consulta local: primeira etapa de desempenho

**Objetivo:** medir consultas com dados sintéticos e corrigir os custos de busca por número e ordenação comprovados nessa medição.

**Arquitetura:** manter FastAPI, consultas locais e SQLite/SQLCipher. Concentrar os índices de manifestações em um módulo usado pelas duas entradas de escrita: sincronização e importação. As consultas continuam somente leitura e não criam índices ao navegar.

**Escopo aprovado:** começar por medições e consultas do banco, preservando filtros, resultados e funcionamento fiscal. A separação das rotas e a avaliação de concorrência serão etapas posteriores.

**Restrições:** sem dependências novas, sem dados reais ou chamadas à SEFAZ, sem alterar regras de cancelamento/CC-e, sem cache dos resultados fiscais, sem alteração direta da main.

## Tarefas

- [x] Criar benchmark reproduzível em `scripts/benchmark_queries.py`: bases temporárias, tamanhos configuráveis, consultas simples/agrupadas, primeira página, página profunda e número; registrar mediana, tamanho do banco e tempo de inserção. Comparar esquema anterior e candidato com a mesma carga.
- [x] Escrever e executar testes que mostrem o custo de varredura e ordenação; validar migração repetida de banco existente e compatibilidade de consulta sem os índices novos.
- [x] Criar `nfe_consulta/indices_consulta.py` e integrar em `BancoManifestacoes._criar_schema` e `web.xml_store._schema`. Criar apenas índices ausentes nos fluxos de escrita.
- [x] Repetir benchmark; registrar resultados e limites em `docs/desempenho-consultas.md`. Medir custo de escrita e armazenamento, além da leitura.
- [x] Executar suíte Python e testes JavaScript; revisar diff e concluir commit local com evidências.
- [x] Enviar a branch e disponibilizar PR: publicação autorizada pelo usuário; [PR #15](https://github.com/MagroNego/ConsultaManifestacao_DFE/pull/15) aberto para a main.

## Verificação

Testes de desempenho usam orçamento de instruções SQL, sem limites de tempo sujeitos à máquina. Testes funcionais verificam mesmos totais, ordem, filtros e CC-e antes/depois. Incluir banco anterior sem índices, reabertura de esquema e importação. O benchmark não aceita caminho de banco de produção e só remove índices em arquivos temporários próprios.

## Progresso

Base de trabalho: `0814b7a` (main 2.7.5). Amostra exploratória de 100 mil eventos: primeira página ~21 ms, página 1000 ~52 ms, número ~214 ms; agrupadas ~22/54/263 ms. Resultados exploratórios não são comparação final. Testes focados iniciais: 9 passaram. Implementação nesta sessão.

Resultado final: 457 testes Python passaram (449 avisos de depreciação já existentes na base), 5 testes JavaScript passaram e compileall/diff-check passaram. Testes novos antes dos índices falharam nos custos e planos esperados. A contagem agrupada residual exige orçamento de 80 mil instruções no teste com dez mil eventos, contra cerca de 132 mil no esquema anterior; o caso sem agrupamento usa limite de dez mil. Uma variante de índice cobrindo chave/código foi descartada porque prejudicava o plano da listagem geral. Comparação final: nove amostras alternadas, dez mil/cem mil notas com 10% de CC-e, resultados completos iguais entre esquemas. Revisão independente sem achados críticos/importantes; implementação feita nesta sessão.

Código e relatório no commit `c09dcd1`, branch `codex/query-performance`. Nenhuma integração na main. O envio inicial por `git push` foi rejeitado pela revisão automática por considerar insuficiente a autorização de publicação. Após autorização explícita do usuário, o Git do terminal não encontrou credenciais; a integração GitHub publicou a mesma árvore validada (`af4c20c8f1db268355cedd8931ec9d2cb26dbf3b`) e abriu o PR #15, sem merge.
