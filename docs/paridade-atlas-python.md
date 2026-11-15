# Paridade com o Atlas em Python

Comparação de código entre a versão Python em `main` (`2f813ad`) e a migração Next.js/.NET na PR #20 (`5ba5a06` na revisão inicial). A tabela distingue funcionalidades disponíveis de fluxos que ainda precisam ser portados. A validação operacional com o banco, o certificado e o SMTP da empresa é uma etapa separada.

| Área | Já existe na migração | Diferenças para o Python |
| --- | --- | --- |
| Consulta de manifestações | Busca por número ou chave NF-e, eventos e CT-e vinculados por nota, CC-e com texto e protocolo, exportação XLSX. | Filtros combinados por período do evento, tipo de manifestação e situação, além do parâmetro de série do legado. O Python pagina e totaliza eventos filtrados. A migração pagina notas por número ou chave. |
| Consulta por arquivo | Busca individual. | Upload de `CHAVES.txt` ou `NOTAS.txt`, validação da lista e geração de relatório em lote. |
| Emissão | Lista de notas com busca, período e situação. Itens e eventos por nota. XLSX de notas e XLSX avulso dos itens. Há valores de PIS e COFINS retidos no Excel de notas. | Relatórios gerais de itens e impostos e de PIS/COFINS retidos, filtro independente de item, filtro de itens dentro da nota e CSV. O XLSX geral do Python reúne abas Notas, Itens, Retenções e Resumo. O XLSX .NET de Emissão hoje tem apenas a aba Emissão. |
| Arquivos gerados | Exportações diretas da Consulta e da Emissão. | Central de Downloads, histórico e recuperação de arquivo gerado, notificações, progresso e cancelamento das tarefas em segundo plano. Nomes de relatórios com o período deram lugar a nomes fixos. |
| Status SEFAZ | Distribuição, recuperação por NSU, agendador e dados operacionais no serviço .NET. Fila pontual por NSU no Admin. | Tela Status com disponibilidade do banco, última gravação, `ultNSU`, `maxNSU`, janela de sincronização, respostas pendentes e logs de execução com diagnóstico. O endpoint `/health` apenas responde pela saúde do processo. |
| Controles SEFAZ | Sincronização manual e recuperação por CLI. Horários, SMTP e certificado por configuração do serviço. | Iniciar sincronização pela web, editar agendamento pela web, configurar ou testar alertas por e-mail e reenviar pendências pela interface. Também faltam os fluxos web de configuração do certificado e substituição do banco do legado. O .NET lê certificado de arquivo ou, no Windows, do repositório por thumbprint. |
| Importação de XML/ZIP | Upload administrativo de XML/ZIP, incluindo eventos vinculados de cancelamento e CC-e. Retorno com totais de importados e existentes. | Histórico e registro baixável por lote, com contagem separada de cancelamentos, CC-e, duplicatas e erros. O Python aceita 100 MiB no envio e 250 MiB descompactados. A migração aplica 64 MiB expandidos e 8 MiB por XML, com transação por lote. |
| Situação manual | Marcar ou remover cancelamento local com motivo e gravar histórico. | Operação em lote com até 100 notas no Python. O endpoint .NET aceita uma única nota ou chave por requisição. |
| Contas | Login por e-mail, cadastro por administrador, senha, bloqueio, liberação e auditoria de contas. | Cadastro pelo próprio usuário, login por nome de usuário, edição de e-mail, edição do perfil na tela e exclusão de conta. O Python tem perfis consulta, fiscal e admin. A UI .NET diferencia uma conta comum de uma administrativa. O histórico administrativo geral do Python é mais amplo que a auditoria de contas apresentada na migração. |
| Aging List | Conversão de PDF ou XLSX para Excel. A migração também aceita TXT. | O Python oferece prévia e arquivo TXT de diagnóstico quando a conversão falha. A migração mostra a mensagem de erro. Ainda falta comparar os XLSX finais com amostras reais do Infor LN. |

A busca direta geral por CT-e e o envio de manifestação à SEFAZ não apareceram como telas do Python auditado. O CT-e já era consultado pelo vínculo com a NF-e. Esses dois pedidos seriam extensões além da paridade acima.

## Ordem sugerida para a virada

1. Recuperar os filtros da Consulta e a geração pelas listas TXT. Conferir os resultados e o Excel das duas versões com uma cópia da mesma base.
2. Recuperar os relatórios e formatos de Emissão. Comparar abas, colunas, retenções e totais com o legado.
3. Expor Status, logs e controles SEFAZ a partir dos serviços .NET existentes. Conferir cursor, janela, respostas pendentes e sincronizações de teste.
4. Recuperar Downloads, notificações e registro dos lotes. Validar cancelamento das tarefas, erros e entrega de arquivos.
5. Fechar as operações administrativas que ainda forem necessárias na rotina. Verificar perfis, contas, cancelamento manual em lote e diagnóstico do Aging.

## Código consultado

- Python. [Rotas da aplicação](https://github.com/MagroNego/AtlasDFE/blob/2f813ad/nfe_consulta/web/app.py), [rotas de Emissão](https://github.com/MagroNego/AtlasDFE/blob/2f813ad/nfe_consulta/web/xml_routes.py) e [telas do legado](https://github.com/MagroNego/AtlasDFE/tree/2f813ad/nfe_consulta/web/templates).
- Migração. [Rotas principais e serviços](../backend/Atlas.Api/Program.cs), [endpoints fiscais](../backend/Atlas.Api/FiscalEndpoints.cs), [administração](../backend/Atlas.Api/Administration.cs), [importação de Emissão](../backend/Atlas.Infrastructure/EmissionStore.cs) e [rotas web](../frontend/app).
