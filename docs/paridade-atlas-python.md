# Paridade com o Atlas em Python

Comparação de código entre a versão Python em `main` (`2f813ad`) e a migração Next.js/.NET na PR #20 (`5ba5a06` na revisão inicial). A tabela distingue funcionalidades disponíveis de fluxos que ainda precisam ser portados. A validação operacional com o banco, o certificado e o SMTP da empresa é uma etapa separada.

| Área | Já existe na migração | Diferenças para o Python |
| --- | --- | --- |
| Consulta de manifestações | Filtros combinados por número, chave, período do evento, manifestação e situação. Eventos filtrados em tela e no XLSX, com atalhos para a CC-e e os CT-e vinculados. | O Python pagina eventos. A migração agrupa os eventos por NF-e e pagina notas. O filtro de série do legado permanece fora da tela. |
| Consulta por arquivo | Upload de `CHAVES.txt` ou `NOTAS.txt`, validação em UTF-8, deduplicação das entradas e Excel com as ocorrências. O lote de números lista todos os resultados e a contagem por número, inclusive os não encontrados. | O download é direto. A fila e o arquivamento central dos relatórios continuam pendentes. |
| Emissão | Relatórios de notas, itens e impostos e PIS/COFINS retidos, com busca, período, situação e filtro independente de item. Itens filtráveis dentro da nota. Excel com Notas, Itens, Retencoes e Resumo, CSV do relatório selecionado e arquivos avulsos dos itens. Cabeçalhos e valores sem decoração. | Comparar os relatórios com a base operacional antes da virada. XMLs importados por versões .NET anteriores precisam de reimportação do mesmo arquivo para recuperar hora, pesos e dados do destinatário ausentes. Dados que não constam no XML permanecem vazios. |
| Arquivos gerados | Exportações diretas da Consulta e da Emissão. | Central de Downloads, histórico e recuperação de arquivo gerado, notificações, progresso e cancelamento das tarefas em segundo plano. Os arquivos de manifestação e Emissão incluem o período no nome. |
| Status SEFAZ | Distribuição, recuperação por NSU, agendador e dados operacionais no serviço .NET. Fila pontual por NSU no Admin. | Tela Status com disponibilidade do banco, última gravação, `ultNSU`, `maxNSU`, janela de sincronização, respostas pendentes e logs de execução com diagnóstico. O endpoint `/health` apenas responde pela saúde do processo. |
| Controles SEFAZ | Sincronização manual e recuperação por CLI. Horários, SMTP e certificado por configuração do serviço. | Iniciar sincronização pela web, editar agendamento pela web, configurar ou testar alertas por e-mail e reenviar pendências pela interface. Também faltam os fluxos web de configuração do certificado e substituição do banco do legado. O .NET lê certificado de arquivo ou, no Windows, do repositório por thumbprint. |
| Importação de XML/ZIP | Upload administrativo de XML/ZIP, incluindo eventos vinculados de cancelamento e CC-e. Retorno com totais de importados e existentes. | Histórico e registro baixável por lote, com contagem separada de cancelamentos, CC-e, duplicatas e erros. O Python aceita 100 MiB no envio e 250 MiB descompactados. A migração aplica 64 MiB expandidos e 8 MiB por XML, com transação por lote. |
| Situação manual | Marcar ou remover cancelamento local com motivo e gravar histórico. | Operação em lote com até 100 notas no Python. O endpoint .NET aceita uma única nota ou chave por requisição. |
| Contas | Login por e-mail, cadastro por administrador, senha, bloqueio, liberação e auditoria de contas. | Cadastro pelo próprio usuário, login por nome de usuário, edição de e-mail, edição do perfil na tela e exclusão de conta. O Python tem perfis consulta, fiscal e admin. A UI .NET diferencia uma conta comum de uma administrativa. O histórico administrativo geral do Python é mais amplo que a auditoria de contas apresentada na migração. |
| Aging List | Conversão de PDF ou XLSX para Excel. A migração também aceita TXT. | O Python oferece prévia e arquivo TXT de diagnóstico quando a conversão falha. A migração mostra a mensagem de erro. Ainda falta comparar os XLSX finais com amostras reais do Infor LN. |

A busca direta geral por CT-e e o envio de manifestação à SEFAZ não apareceram como telas do Python auditado. O CT-e já era consultado pelo vínculo com a NF-e. Esses dois pedidos seriam extensões além da paridade acima.

## Ordem sugerida para a virada

1. Conferir os filtros e o Excel da Consulta, por tela e por TXT, nas duas versões com uma cópia da mesma base.
2. Conferir os relatórios de Emissão nas duas versões com a base operacional. Os formatos, filtros e quatro abas já foram recuperados e verificados com dados sintéticos.
3. Expor Status, logs e controles SEFAZ a partir dos serviços .NET existentes. Conferir cursor, janela, respostas pendentes e sincronizações de teste.
4. Recuperar Downloads, notificações e registro dos lotes. Validar cancelamento das tarefas, erros e entrega de arquivos.
5. Fechar as operações administrativas que ainda forem necessárias na rotina. Verificar perfis, contas, cancelamento manual em lote e diagnóstico do Aging.

## Código consultado

- Python. [Rotas da aplicação](https://github.com/MagroNego/AtlasDFE/blob/2f813ad/nfe_consulta/web/app.py), [rotas de Emissão](https://github.com/MagroNego/AtlasDFE/blob/2f813ad/nfe_consulta/web/xml_routes.py) e [telas do legado](https://github.com/MagroNego/AtlasDFE/tree/2f813ad/nfe_consulta/web/templates).
- Migração. [Rotas principais e serviços](../backend/Atlas.Api/Program.cs), [endpoints fiscais](../backend/Atlas.Api/FiscalEndpoints.cs), [administração](../backend/Atlas.Api/Administration.cs), [importação de Emissão](../backend/Atlas.Infrastructure/EmissionStore.cs) e [rotas web](../frontend/app).

## Relatórios de Emissão recuperados

- `GET /api/emissao` mantém a lista de notas e aceita também `item` (código, descrição ou NCM). `GET /api/emissao/relatorio` recebe `tipo=notas|itens|retencoes` e os mesmos filtros `busca`, `inicio`, `fim`, `situacao`, `item` e `pagina`. A página mostra até 100 linhas, sem contador nem série.
- `GET /api/emissao/excel` gera quatro abas em uma transação de leitura consistente. `GET /api/emissao/csv` exporta apenas o tipo selecionado. As rotas `/{chave}/itens/excel` e `/{chave}/itens/csv` respeitam o filtro de item da nota. Downloads autenticados, com erro apresentado na própria tela e teto de 100.000 linhas por relatório.
- Retenções são valores por nota e só geram linha se PIS ou COFINS retido for positivo. O Resumo segue o Python, uma linha por item com os pesos totais declarados na nota, sem rateio. Não representa um totalizador de pesos por item.
- XMLs novos guardam hora local, soma dos volumes, CNPJ/CPF do destinatário e UF nos itens. Reimportar o mesmo XML (mesmo hash e chave) enriquece dados de versões anteriores sem duplicar notas. Um XML diferente com a mesma chave não substitui o documento existente.
- Chaves, NCM, CFOP e códigos permanecem texto. Valores numéricos do legado em pt-BR são normalizados. CSV usa UTF-8 com BOM, ponto e vírgula e campos entre aspas, com proteção para textos interpretáveis como fórmula. Excel mantém literais sem fórmulas e omite as mesmas seis colunas técnicas que o Python.
