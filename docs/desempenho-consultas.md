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

## Segunda etapa: contagem agrupada

A contagem passou a agrupar os eventos filtrados por chave e somar a quantidade de manifestações diferentes de CC-e. Uma chave sem manifestação contribui com uma linha, mesmo quando há várias cartas. Uma chave com duas manifestações continua contribuindo com duas linhas. Isso calcula o mesmo total sem montar listas de inclusão/exclusão nem selecionar o ID da carta para contar.

A escolha dos IDs da página, a ordenação e a exibição das cartas permanecem iguais. Esta etapa não acrescenta índices, dependências ou cache.

Comparação específica da segunda etapa, no mesmo banco, com nove amostras alternadas. A referência é o código do PR #15, commit `6a1ff061f10269bf23c5e3af8237dea363aa7e9f`, já com os índices da primeira etapa. Os tempos medem a consulta completa com CC-e agrupada, incluindo a seleção e o enriquecimento das linhas.

| Consulta | Notas | PR #15, antes da segunda etapa | Contagem nova |
|---|---:|---:|---:|
| Primeira página | 10.000 | 29,256 ms | 19,562 ms |
| Página 250 | 10.000 | 42,683 ms | 27,353 ms |
| Número | 10.000 | 14,210 ms | 13,146 ms |
| Período e série | 10.000 | 12,262 ms | 11,727 ms |
| Primeira página | 100.000 | 206,293 ms | 152,626 ms |
| Página 2500 | 100.000 | 232,941 ms | 172,847 ms |
| Número | 100.000 | 57,282 ms | 38,574 ms |
| Período e série | 100.000 | 73,566 ms | 72,327 ms |

Estes resultados vêm de outra execução, com carga e cache próprios do host; a comparação válida é entre as duas colunas desta tabela, não entre esta referência e os números da primeira etapa. As limitações de ambiente e do conjunto sintético continuam as mesmas. Todas as amostras verificam a igualdade do resultado completo. [Dados da comparação](benchmarks/2026-11-04-contagem-agrupada.json).

Para reproduzir, com o commit de referência disponível no Git local:

```bash
python -m scripts.benchmark_grouped_count --sizes 10000 100000 --repeats 9 > benchmark-contagem.json
```

O script lê a versão de referência pelo hash do commit e executa as duas funções sobre o mesmo banco temporário. Não altera nem abre um banco do usuário. Os nove casos adicionais cobrem leitura única da fonte no plano da contagem, mais de uma manifestação por nota, cartas sem manifestação, filtro que encontra só cartas, datas, número e resultado vazio. A suíte existente cobre situação de cancelamento e banco criptografado.

Após a segunda etapa, 466 testes Python e 5 testes JavaScript passaram localmente; a revisão independente não encontrou problemas importantes ou críticos.

## Trabalho restante

A contagem continua proporcional ao conjunto filtrado, e a seleção dos IDs da página ainda usa a consulta anterior. Uma experiência que também substituía essa seleção por agregação piorou a paginação; ela foi descartada.

## Terceira etapa: HTML e concorrência

A medição passou a incluir o tratamento da requisição, as consultas, a renderização dos templates e o corpo HTML completo. O transporte é `httpx.ASGITransport` no processo: não inclui socket TCP, parser HTTP do Uvicorn, rede, download de recursos estáticos, JavaScript ou pintura da tela. Portanto, não mede do clique até os documentos aparecerem no computador do usuário.

O teste usa bancos temporários com dez mil e cem mil notas, uma ciência por nota, CC-e e informação de emitente em 10% delas, três séries e datas distribuídas em 28 dias. Para a Emissão, também prepara metadados sintéticos em `xml_documentos`. Consulta exibe cem linhas por página. Cada rota tem aquecimento fora das amostras; a execução sequencial usa três respostas medidas, e a concorrente usa três grupos de quatro requisições (doze respostas individuais). O relatório mantém as amostras, os tempos por etapa e hashes das fontes. Os tempos das etapas podem estar aninhados; não devem ser somados.

Dois problemas foram corrigidos:

1. A Consulta abria o SQLCipher para verificar disponibilidade e novamente para pesquisar. O novo `web/consulta_data.py` abre uma conexão somente leitura por pedido, verifica e consulta no mesmo worker, e fecha a conexão antes de devolver os dados. A senha é lida uma vez por pedido, sem cache e sem conexão persistente.
2. As rotas gravavam auditoria diretamente no loop assíncrono. Trinta e sete chamadas agora aguardam a gravação em uma thread. Um pedido que precisa auditar ainda aguarda sua gravação, mas o loop não fica preso no I/O do log. Não há fila de descarte nem nova garantia de gravação em caso de falha do disco ou perda do processo.

Quatro testes de concorrência exercitam requisições reais: consulta durante exportação XLSX, durante importação HTTP e durante uma transação SQLite protegida pela trava de sincronização; e resposta independente enquanto o stream real do log está pausado. Os três primeiros usam eventos para manter o trabalho em andamento e confirmar que a Consulta responde antes da liberação. O teste de escrita verifica que dados não confirmados ficam invisíveis e que o próximo pedido vê o commit. Eles não reproduzem uma comunicação real com a SEFAZ nem uma carga ilimitada de usuários.

Quatorze testes específicos verificam abertura única em SQLite/SQLCipher, fechamento no mesmo worker, conexão emprestada, resultado fresco no pedido seguinte, banco ausente sem criação de arquivo, legado sem prontidão, fallback de XML importado, falha da consulta, senha errada e arquivo de senha vazio/inacessível. A rotação de senha entre pedidos é relida sem cache.

O Chromium local via Playwright não iniciou: `process_singleton_posix.cc:297 socket() failed: Operation not permitted`, com `ptrace` também negado. Não houve teste visual nem medição de pintura no navegador; nenhum contorno da restrição foi tentado.

Reprodução da resposta da aplicação, na raiz do repositório com suas dependências instaladas:

```bash
python -m scripts.benchmark_web_response --sizes 10000 --repeats 3 --encrypted
python -m scripts.benchmark_web_response --sizes 100000 --repeats 3
```

O script usa apenas caminhos temporários e não transmite NF-e nem envia e-mail. A opção de servidor local, quando necessária, usa uma fixture e se limita a `127.0.0.1`. [Amostras HTTP e limites do ambiente](benchmarks/2026-11-04-web-response.json).

Resultados com dez mil notas e SQLCipher, comparando a revisão que já executava auditoria em threads com a conexão única:

| Rota | Simultâneas | Antes da conexão única | Conexão única |
|---|---:|---:|---:|
| Consulta, primeira página | 1 | 382,223 ms | 209,865 ms |
| Consulta, página 2 | 1 | 379,887 ms | 212,168 ms |
| Consulta, primeira página | 4 | 554,797 ms | 407,870 ms |
| Consulta, página 2 | 4 | 524,225 ms | 398,063 ms |

Com cem mil notas e SQLite sem criptografia, a revisão final gerou a primeira página de Consulta em 94,744 ms e a segunda em 97,623 ms; com quatro pedidos simultâneos, as medianas individuais foram 179,164 e 154,849 ms. Esses números não devem ser comparados diretamente com os de SQLCipher: os tamanhos e os motores são diferentes. As execuções antes/depois ocorreram em blocos, não em amostras alternadas, e o host teve variação de carga. A economia observada em SQLCipher corresponde à remoção de uma segunda abertura, não a uma estimativa de ganho em produção.

O runtime criptografado medido usa `sqlcipher3-binary` 0.6.0, SQLCipher 4.12.0, SQLite 3.51.1 e 256.000 iterações de KDF com PBKDF2-HMAC-SHA512. Nenhum desses parâmetros foi alterado pela otimização. As dependências declaradas do Atlas permanecem iguais. O script registra os metadados do motor fora do intervalo medido.

Após esta etapa, 484 testes Python e cinco testes JavaScript passaram localmente. A revisão independente identificou e corrigiu durante a implementação a resolução de senha fora do worker, que poderia transformar arquivo de senha vazio/inacessível em erro HTTP 500. Os testes finais preservam o aviso amigável e não houve achado importante ou crítico pendente.

Permanece necessário medir a rede e o navegador do ambiente de uso, e a capacidade do pool de threads continua limitada. A reorganização ampla das rotas do `app.py` também continua pendente. A terceira etapa separa a coordenação dos dados da Consulta; as aberturas adicionais da Emissão foram tratadas na quarta etapa abaixo.

## Quarta etapa: Emissão e detalhes

`web/emissao_data.py` coordena a leitura da listagem, dos itens e das manifestações usando uma conexão somente leitura por pedido. A listagem antes abria duas conexões (relatório e CC-e); os itens abriam duas (nota e itens); as manifestações abriam três (nota, verificação da tabela e eventos). Agora cada fluxo abre uma conexão e a fecha no mesmo worker.

As funções públicas `query_report` e `consultar_cartas` continuam responsáveis por abrir e fechar suas conexões. As novas funções com sufixo `na_conexao` permitem compartilhar a conexão sem tomar sua propriedade. O SQL e a montagem dos relatórios foram preservados; exportações continuam usando a interface anterior. Não há conexão persistente, cache fiscal, dependência ou índice novo nesta etapa.

A ordem de validação da listagem continua sendo leitura da senha, validação dos filtros e abertura do banco. Filtros inválidos não tentam abrir um banco com senha errada. Nos detalhes, nota inexistente continua retornando 404 antes de avaliar filtros adicionais. Arquivo de senha vazio ou inacessível nesses detalhes agora retorna mensagem genérica 400, em vez de erro interno 500. Se falhar apenas o enriquecimento de CC-e, a listagem já carregada continua visível com a mensagem de erro.

Comparação da resposta HTML completa em SQLCipher com dez mil notas, três amostras sequenciais ou doze respostas em três grupos de quatro, após aquecimento:

| Página | Simultâneas | Antes | Conexão única |
|---|---:|---:|---:|
| Listagem de Emissão | 1 | 239,143 ms | 139,363 ms |
| Página 2 da Emissão | 1 | 254,486 ms | 140,393 ms |
| Itens da nota | 1 | 223,385 ms | 121,019 ms |
| Manifestações da nota | 1 | 360,923 ms | 129,799 ms |
| Listagem de Emissão | 4 | 309,752 ms | 174,150 ms |
| Página 2 da Emissão | 4 | 327,021 ms | 215,006 ms |
| Itens da nota | 4 | 308,206 ms | 148,541 ms |
| Manifestações da nota | 4 | 424,487 ms | 159,042 ms |

O benchmark contabilizou as aberturas por pedido e verificou hashes do HTML completo. Todas as respostas foram idênticas entre versões nos cenários medidos, incluindo item e CC-e sintéticos. Cada versão executa em um processo novo com suas próprias configurações e banco temporário; os hashes das fontes são conferidos antes e depois da medição. A versão anterior é o commit público `d872efa7ef1340ddd425d1e36837cf0152abde91`.

Os tempos vêm de uma execução diferente da terceira etapa. A comparação válida é entre as colunas desta tabela: o host e sua carga variam. O transporte continua ASGI no processo, sem TCP, parser HTTP, rede, recursos estáticos, JavaScript ou pintura no navegador; a amostra é exploratória, sem p95 e sem estimativa de capacidade em produção. [Dados completos](benchmarks/2026-11-04-emissao-response.json).

Para reproduzir, com o commit de referência disponível no Git local e as dependências do Atlas instaladas:

```bash
python -m scripts.benchmark_emissao_response --size 10000 --repeats 3
```

O padrão usa SQLCipher. `--engine plain` mede SQLite sem criptografia. O script não abre dados do usuário, não chama a SEFAZ e não altera parâmetros da criptografia. A etapa mantém testes específicos de abertura/fechamento e worker em SQLite/SQLCipher, conexão emprestada, ausência do banco, senha e sua rotação, filtros, precedência de erros, CC-e, resultado fresco e falha de enriquecimento.

Validação final da quarta etapa: 512 testes Python e cinco testes JavaScript passaram localmente; revisão independente sem achados importantes ou críticos pendentes. Durante a revisão, a validação da listagem foi reposicionada antes da abertura, preservando a mensagem de filtro inválido mesmo quando a senha está errada.
