# Modernização do frontend do Atlas DFE

## Base auditada

A versão operacional em `main` (`2f813ad`) usa FastAPI, templates Jinja e JavaScript nativo. A migração em `refactor/arquitetura-oculus` (`e112397`) usa Next.js 16, React 19 e TypeScript com API ASP.NET Core 10 e PostgreSQL. A modernização parte dessa migração. A branch `main` permanece na versão operacional.

A referência visual veio de `nfe_consulta/web/templates/base.html`, `static/app.css` e `static/motion.css` do legado, incluindo o layout compacto de `56002d8` e as microinterações de `5540e37`. A navegação centralizada, os logos Atlas e Yorozu, a paleta azul-ardósia, os temas claro e escuro e a preferência `nfe-theme` voltaram à interface. Os logos foram redimensionados em WebP sem perdas a partir dos arquivos do repositório.

## Cobertura operacional

| Fluxo | Atlas em Python | Migração em .NET e frontend desta branch |
| --- | --- | --- |
| Consulta de NF-e | Filtros por período, empresa, situação, manifestações e demais parâmetros. | Número ou chave NF-e, 25 notas por página, eventos, protocolo, texto de CC-e, CT-e vinculados e Excel do resultado. A API atual aceita `busca` e `pagina`. |
| Manifestação | Histórico e filtros de eventos recebidos. | Histórico e exportação ligados à nota consultada. A API revisada não expõe envio de manifestação individual ou em massa. |
| CT-e | Vínculos de CT-e na Consulta. | Vínculos, chave, situação, transportadora e data quando fornecida. A busca direta pelo modelo 57 exige mudança em `IdentificadorNota` e consulta própria. |
| Cancelamentos e CC-e | Status e históricos, marcação local e ações de CC-e. | CC-e identificada na linha com acesso ao texto e protocolo. Cancelamento local e histórico disponíveis através de `/api/admin/situacao`. O painel independente de Status e os relatórios de lotes do legado não foram portados nesta etapa. |
| Emissão | Notas, filtros, itens, XML, exportação e atalhos para eventos. | Notas paginadas em até 100, filtros combinados de período/situação/texto, itens sob demanda, importação XML/ZIP para administrador e Excel de notas e itens. A ação Eventos consulta o histórico local pela chave da nota e abre CC-e, protocolos e CT-e vinculados na própria linha. |
| Aging List | Conversão do Infor LN e entrega via central de downloads. | Conversão TXT, XLSX ou PDF via `/api/aging` e download direto. |
| Downloads e notificações | Fila, histórico, progresso e cancelamento da geração. | A API entrega Excel diretamente. A central de Downloads e o sino de notificações dependem de um contrato de tarefas e histórico. |
| Administração | Contas, histórico, fila NSU, agenda, operações manuais de sincronização, certificado e alertas. | Contas, auditoria, situação manual e fila NSU estão disponíveis via HTTP. Agenda, alertas e sincronização existem na camada de serviço/configuração em C#. As ações e diagnósticos completos na interface dependem de endpoints administrativos específicos. |
| Autenticação | Modos local e corporativo definidos na instalação. | Sessão ASP.NET Identity, perfil Administrador e token CSRF. As contas do legado precisam de cadastro na nova versão. |

Seleção em massa, ordenação global, redimensionamento e menus contextuais ficaram fora desta etapa. Para documentar equivalência de operação, a validação de virada deve cobrir os contratos ausentes e os fluxos que a equipe usa com a base e o certificado da instalação.

## Implementação

- O layout raiz mantém uma sessão para as rotas, usa navegação do Next.js e disponibiliza logout global. A interface usa o perfil para mostrar ações administrativas. A autorização das requisições continua nas políticas da API.
- Consulta e Emissão têm cabeçalhos fixos e rolagem delimitada. As páginas mantêm os limites da API. Na Consulta, apenas a nota aberta monta os detalhes. Na Emissão, itens são consultados ao abrir e as colunas são calculadas uma vez por renderização do resultado.
- A restauração de tema roda antes da interface. Os logotipos são importados como ativos estáticos do Next.js e o efeito de abertura dos detalhes respeita `prefers-reduced-motion`.
- O frontend continua usando a mesma origem para sessão, CSRF, uploads e downloads. Nenhum contrato de API ou dependência de produção foi alterado.

## Verificação

- `npm ci`, `npm run typecheck` e `npm run build` passaram. O frontend ainda não tem script de lint configurado.
- `.NET 10.0.404` compilou a solução e a suíte existente sem erros ou avisos. O PostgreSQL 18.6 recebeu as migrações e passou as verificações de replay, deduplicação e preservação de cursor de `Atlas.Sefaz.Checks`.
- Chromium com Playwright, em 1440 × 900 e 390 × 844, verificou Consulta, CC-e, CT-e vinculados, paginação, exportação, tema persistente, movimento reduzido, Emissão, Aging, navegação administrativa e ausência de estouro horizontal da página. Essa comparação usou respostas sintéticas.
- O teste no navegador com a API .NET real e PostgreSQL local verificou login e logout, respostas 401 e 403, recusa de alteração sem CSRF, consulta e CC-e, três downloads XLSX, importação XML, itens, situação manual e criação de operador. O teste usou dados fictícios e execução local.
- A suíte da `main` passou com 518 testes Python e cinco casos reportados pelo executor JavaScript. Os quatro arquivos de teste JavaScript legados ainda presentes na árvore .NET referenciam scripts removidos em `e112397` e falham ao carregá-los. O workflow .NET executa a suíte C# e os comandos de verificação do Next.js.
- Na comparação com os mesmos 25 resultados sintéticos, a Consulta renderizou 322 elementos DOM, ante 494 na migração original. A árvore fechada passou de 50 listas de eventos e vínculos para zero. A soma dos quatro logos passou de 963.754 para 20.478 bytes em disco. Essas medições não representam latência da API ou tempo de renderização em produção.

A conversão de Aging via navegador foi exercitada com resposta simulada. O ambiente corporativo, os arquivos reais do Infor LN, a base operacional, o certificado, o SMTP e a comunicação com a SEFAZ continuam pendentes de validação.
