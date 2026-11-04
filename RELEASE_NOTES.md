# AtlasDFE 2.7.5

- Removida a exigência de senha no modo padrão, inclusive para Admin, importações e alteração manual da situação de notas.
- Não é necessário executar CONFIGURAR_ADMIN.cmd. Quem alcançar o aplicativo pela rede terá acesso administrativo.
- Contas antigas preservadas para o modo opcional de contas locais. Banco fiscal, certificado, agenda e layout compacto preservados.
- CSRF, limites de upload, janela SEFAZ, cooldown e trava continuam ativos.

## Paginação

Próximo e Anterior na Consulta selecionam os eventos da página antes de carregar detalhes. A identificação de notas importadas reaproveita a conexão do banco. Emissão não lê mais o histórico de lotes fora da tela Importar. Filtros, totais, ordem e eventos de CC-e foram preservados.

## Navegação

A Consulta evita o diagnóstico completo de NSUs ao abrir. CSS e JavaScript versionados são reutilizados pelo navegador. A exibição do certificado reaproveita metadados por até 30 segundos, com invalidação ao mudar arquivo ou senha; a sincronização e a validação continuam lendo o certificado diretamente. O ganho depende do banco e do computador.

## Atualização

Pare o app, preserve banco, dados, secrets, certificado e logs, atualize o código e reinicie pelo lançador habitual. Use Ctrl+F5 para atualizar os recursos da interface. Instalações com NFE_WEB_LOCAL_AUTH=1 devem alterar para 0 para usar o acesso sem senha.
