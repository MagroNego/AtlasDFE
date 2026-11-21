# Paridade com o Atlas em Python

Comparação de código entre a versão Python em `main` (`2f813ad`) e a migração Next.js/.NET na PR #20 (`5ba5a06` na revisão inicial). A tabela distingue funcionalidades disponíveis de fluxos que ainda precisam ser portados. A validação operacional com o banco, o certificado e o SMTP da empresa é uma etapa separada.

| Área | Já existe na migração | Diferenças para o Python |
| --- | --- | --- |
| Consulta de manifestações | Filtros combinados por número, chave, período do evento, manifestação e situação. Eventos filtrados em tela e no XLSX, com atalhos para a CC-e e os CT-e vinculados. | O Python pagina eventos. A migração agrupa os eventos por NF-e e pagina notas. O filtro de série do legado permanece fora da tela. |
| Consulta por arquivo | Upload de `CHAVES.txt` ou `NOTAS.txt`, validação em UTF-8, deduplicação das entradas e Excel com as ocorrências. O lote de números lista todos os resultados e a contagem por número, inclusive os não encontrados. | A geração é feita pela Central de Downloads com o mesmo conteúdo do Excel direto. |
| Emissão | Relatórios de notas, itens e impostos e PIS/COFINS retidos, com busca, período, situação e filtro independente de item. Itens filtráveis dentro da nota. Excel com Notas, Itens, Retencoes e Resumo, CSV do relatório selecionado e arquivos avulsos dos itens. Cabeçalhos e valores sem decoração. | Comparar os relatórios com a base operacional antes da virada. XMLs importados por versões .NET anteriores precisam de reimportação do mesmo arquivo para recuperar hora, pesos e dados do destinatário ausentes. Dados que não constam no XML permanecem vazios. |
| Arquivos gerados | Central de Downloads para Consulta, TXT, Emissão e itens da nota, com fila persistente, etapas, cancelamento, histórico individual e aviso no menu. Os arquivos podem ser baixados novamente. | A retenção é de até sete dias e o serviço mantém os 20 pedidos concluídos mais recentes por conta. Os relatórios de importação ficam restritos à conta administrativa que os solicitou. |
| Status SEFAZ | Tela autenticada com cursor, NSU máximo, última gravação de resposta, pendências, fila, janela, agenda e atividade recente de distribuição e recuperação. | A atividade mostra tentativas registradas e retornos persistidos. Os erros de certificado e transporte continuam nos logs do serviço. |
| Controles SEFAZ | Sincronização manual na área administrativa, agenda editável, destinatários e teste SMTP, reagendamento de entregas pendentes e cadastro do certificado A1. | A UF é configurável no painel. O servidor SMTP é configurado na instalação. A Administração importa o banco legado e o Status SEFAZ acompanha a conferência. |
| Importação de XML/ZIP | Registro por lote, contagem separada de notas, cancelamentos, CC-e, duplicatas e erros, e CSV na Central de Downloads. | A migração conserva a transação por lote, o limite de 64 MiB descompactados e 8 MiB por XML. O relatório distingue arquivos rejeitados e registros revertidos. |
| Situação manual | Marcar ou retirar cancelamento local em até 100 notas com motivo e histórico. | A lista inteira é validada antes da gravação. |
| Contas | Perfis Consulta, Fiscal e Administrador, login por e-mail ou usuário, edição de perfil, gestão de contas e solicitação de cadastro opcional com liberação administrativa. | As contas legadas precisam ser cadastradas. Usuários .NET existentes sem perfil explícito mantêm o acesso de Fiscal. |
| Aging List | Conversão PDF, XLSX ou TXT, prévia de lançamentos e diagnóstico baixável em TXT. | Ainda falta comparar os XLSX com amostras reais do Infor LN. |

A busca direta geral por CT-e e o envio de manifestação à SEFAZ não apareceram como telas do Python auditado. O CT-e já era consultado pelo vínculo com a NF-e. Esses dois pedidos seriam extensões além da paridade acima.

## Ordem sugerida para a virada

1. Conferir os filtros e o Excel da Consulta, por tela e por TXT, nas duas versões com uma cópia da mesma base.
2. Conferir os relatórios de Emissão nas duas versões com a base operacional. Os formatos, filtros e quatro abas já foram recuperados e verificados com dados sintéticos.
3. Conferir o Status e os Controles SEFAZ na instalação da empresa, com o certificado e o SMTP corporativos.
4. Ensaiar a importação em um PostgreSQL de homologação e conferir **Status SEFAZ → Integridade da migração**. A comparação registra as contagens normalizadas, as chaves e os NSUs disponíveis.
5. Conferir os perfis, os lotes XML e o Aging com arquivos representativos da rotina.

## Código consultado

- Python. [Rotas da aplicação](https://github.com/MagroNego/AtlasDFE/blob/2f813ad/nfe_consulta/web/app.py), [rotas de Emissão](https://github.com/MagroNego/AtlasDFE/blob/2f813ad/nfe_consulta/web/xml_routes.py) e [telas do legado](https://github.com/MagroNego/AtlasDFE/tree/2f813ad/nfe_consulta/web/templates).
- Migração. [Rotas principais e serviços](../backend/Atlas.Api/Program.cs), [endpoints fiscais](../backend/Atlas.Api/FiscalEndpoints.cs), [administração](../backend/Atlas.Api/Administration.cs), [importação de Emissão](../backend/Atlas.Infrastructure/EmissionStore.cs) e [rotas web](../frontend/app).

## Relatórios de Emissão recuperados

- `GET /api/emissao` mantém a lista de notas e aceita também `item` (código, descrição ou NCM). `GET /api/emissao/relatorio` recebe `tipo=notas|itens|retencoes` e os mesmos filtros `busca`, `inicio`, `fim`, `situacao`, `item` e `pagina`. A página mostra até 100 linhas, sem contador nem série.
- `GET /api/emissao/excel` gera quatro abas em uma transação de leitura consistente. `GET /api/emissao/csv` exporta apenas o tipo selecionado. As rotas `/{chave}/itens/excel` e `/{chave}/itens/csv` respeitam o filtro de item da nota. Downloads autenticados, com erro apresentado na própria tela e teto de 100.000 linhas por relatório.
- Retenções são valores por nota e só geram linha se PIS ou COFINS retido for positivo. O Resumo segue o Python, uma linha por item com os pesos totais declarados na nota, sem rateio. Não representa um totalizador de pesos por item.
- XMLs novos guardam hora local, soma dos volumes, CNPJ/CPF do destinatário e UF nos itens. Reimportar o mesmo XML (mesmo hash e chave) enriquece dados de versões anteriores sem duplicar notas. Um XML diferente com a mesma chave não substitui o documento existente.
- Chaves, NCM, CFOP e códigos permanecem texto. Valores numéricos do legado em pt-BR são normalizados. CSV usa UTF-8 com BOM, ponto e vírgula e campos entre aspas, com proteção para textos interpretáveis como fórmula. Excel mantém literais sem fórmulas e omite as mesmas seis colunas técnicas que o Python.
