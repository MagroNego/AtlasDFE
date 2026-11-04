# Desempenho das consultas locais

Primeira etapa, em 04/11/2026, sobre a main 2.7.5 (`0814b7a`). O objetivo foi medir as consultas existentes e reduzir trabalho de busca e ordenação sem mudar resultados fiscais.

## Alteração

`nfe_consulta/indices_consulta.py` define dois índices: CNPJ/data/NSU numérico/ID e CNPJ/número da NF/data/NSU numérico/ID. As expressões são as mesmas dos filtros e da ordenação existentes. O esquema e o tipo das colunas não mudam.

Os índices são criados pela preparação do banco em `BancoManifestacoes` e pela preparação da importação em `web/xml_store.py`. Em banco existente, a criação ocorre na próxima operação que prepara o banco para escrita, como sincronização ou importação. A primeira criação tem custo; as seguintes apenas verificam a existência dos índices. Reiniciar e navegar, por si só, não cria os índices. As consultas continuam funcionando em bancos antigos antes dessa preparação e não executam DDL.

Uma variante que incluía chave e código no índice por número foi descartada: acelerava a busca específica, mas o plano escolhido pelo SQLite piorava a listagem agrupada sem filtro. Os resultados abaixo usam apenas os dois índices menores da implementação final.

## Como reproduzir

Na raiz de uma cópia do repositório:

```bash
python -m scripts.benchmark_queries --sizes 10000 100000 --repeats 9 > benchmark-consultas.json
```

O script usa a biblioteca padrão do Python e o código do Atlas. Não aceita caminho para banco existente. Cria bancos temporários próprios, prepara uma base sem os índices novos e compara duas cópias idênticas, uma com cada esquema. Remove somente os arquivos temporários próprios ao terminar.

Cada base tem um evento de ciência por nota, com três séries e datas distribuídas em 28 dias. A cada dez notas, inclui uma CC-e e informação de emitente. São 11 mil e 110 mil eventos nas bases de 10 mil e 100 mil notas. O conjunto usa um CNPJ e não inclui CT-e, XMLs completos ou cancelamentos.

As consultas usam 20 linhas por página. A página profunda é a 250 na base menor e a 2500 na maior. O filtro por número busca a nota intermediária, que também tem CC-e. O filtro combinado usa período de 10 a 20/11/2026 e série 2.

São nove amostras por cenário e esquema, alternando a ordem de execução entre anterior e novo. Os tempos abaixo são medianas. Cada amostra compara uma impressão digital do resultado completo com o resultado de referência; divergência de conteúdo, total ou paginação encerra o benchmark com erro. A montagem e comparação da impressão digital ficam fora do tempo medido.

## Resultado com 100 mil notas

Ambiente: Linux x86-64, Python 3.12.14 e SQLite 3.53.1, sem criptografia. [Amostras completas e metadados](benchmarks/2026-11-04-consultas.json).

| Consulta | CC-e agrupada | Esquema anterior | Com índices |
|---|---|---:|---:|
| Primeira página | Não | 13,273 ms | 6,981 ms |
| Página 2500 | Não | 147,782 ms | 14,365 ms |
| Número da NF | Não | 226,347 ms | 3,548 ms |
| Período e série | Não | 77,800 ms | 76,174 ms |
| Primeira página | Sim | 145,551 ms | 132,750 ms |
| Página 2500 | Sim | 341,433 ms | 231,677 ms |
| Número da NF | Sim | 214,384 ms | 53,467 ms |
| Período e série | Sim | 77,356 ms | 67,314 ms |

A Consulta da interface usa o agrupamento de CC-e. Portanto, os resultados sem agrupamento não representam diretamente o tempo dessa tela. Na base menor, o agrupamento teve ganhos pequenos ou variação desfavorável: primeira página de 11,387 para 11,228 ms e página 250 de 14,541 para 15,193 ms. Não há ganho uniforme em todos os cenários.

## Custos

| Medida | 10 mil notas | 100 mil notas |
|---|---:|---:|
| Banco anterior | 4.591.616 bytes | 44.990.464 bytes |
| Banco com índices | 5.844.992 bytes | 57.864.192 bytes |
| Primeira criação dos índices | 22,973 ms | 255,214 ms |
| Inserção de 1.000 eventos, anterior | 13,805 ms | 18,599 ms |
| Inserção de 1.000 eventos, com índices | 19,186 ms | 22,098 ms |

Na base maior, o arquivo cresce aproximadamente 28,6%. Os índices precisam ser mantidos nas escritas; essa medição mostrou aumento de aproximadamente 18,8% no tempo de inserir mil eventos. A inserção inclui commit e usa nove lotes novos de mil eventos em cada cópia; a tabela mostra a mediana. O tamanho foi medido antes desses lotes. O teste não mede o processamento inteiro de XML ou da sincronização.

## Validação e limites

457 testes Python e 5 testes JavaScript passaram localmente. Os sete casos novos verificam orçamento de instruções SQL, ordenação numérica do NSU, preservação de resultados na migração, filtro combinado com série, separação por CNPJ, reabertura idempotente, preparação pela importação e consulta em banco legado sem mudar o esquema. Incluem também migração e consulta de banco protegido com SQLCipher. O runtime local usou `sqlcipher3-binary`; as dependências declaradas do projeto continuam as mesmas.

Os limites de instruções evitam depender da velocidade do host para detectar regressões de busca e ordenação. A contagem agrupada ainda faz uma varredura no índice legado: seu orçamento de teste é maior e esse custo continua explícito.

Estes números medem a função de consulta local, incluindo abertura/fechamento do banco e montagem dos resultados. Não medem HTTP, renderização de HTML, navegador, rede, certificado ou banco real de produção. Os tempos variam com carga e cache do sistema operacional; não representam promessa de latência no Windows ou com SQLCipher. A compatibilidade funcional com SQLCipher foi testada, mas seu desempenho não foi medido aqui. Não houve consulta real à SEFAZ.

## Próxima etapa

A contagem e a seleção das linhas com CC-e agrupada ainda percorrem o conjunto filtrado. Essa é a próxima consulta a analisar, preservando os casos em que existe apenas CC-e e os filtros que localizam a carta e exibem a manifestação principal. A separação das rotas do `app.py` e os testes de concorrência ficam para etapas posteriores.
