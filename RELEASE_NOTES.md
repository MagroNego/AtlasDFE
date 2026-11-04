# AtlasDFE 2.7.3

- Removida a exigência de senha no modo padrão, inclusive para Admin, importações e alteração manual da situação de notas.
- Não é necessário executar CONFIGURAR_ADMIN.cmd. Quem alcançar o aplicativo pela rede terá acesso administrativo.
- Contas antigas preservadas para o modo opcional de contas locais. Banco fiscal, certificado, agenda e layout compacto preservados.
- CSRF, limites de upload, janela SEFAZ, cooldown e trava continuam ativos.

## Atualização

Pare o app, preserve banco, dados, secrets, certificado e logs, atualize o código e reinicie pelo lançador habitual. Use Ctrl+F5 para atualizar os recursos da interface. Instalações com NFE_WEB_LOCAL_AUTH=1 devem alterar para 0 para usar o acesso sem senha.
