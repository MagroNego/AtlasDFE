# AtlasDFE 2.7.2

Aplicação interna para consultar manifestações de NF-e, importar XMLs e gerar relatórios Excel/CSV.

## Ajuste desta versão

Layout desktop mais compacto, com maior largura útil, filtros menores e linhas de tabela de 44 px na Consulta. Em telas largas, os seis filtros ficam em uma linha. O layout móvel mantém os controles existentes.

## O que esta versão entrega

- Consultas, relatórios e downloads sem senha. Admin, importações e cancelamento manual exigem login administrativo.
- Janela SEFAZ configurável no Admin, com padrão 22h–04h de Brasília. Disparos padrão às 22h, 23h, 00h, 01h, 02h e 03h; cooldown de 60 minutos e bloqueios preservados.
- Importação de XMLs e ZIPs, CC-e acessível nas ações da nota e vínculos de CT-e disponíveis no banco.
- Excel de Emissão com Resumo, Notas, Itens e Retencoes. Reimportação completa hora de emissão e pesos ausentes.
- Alertas por e-mail para Operação não Realizada, downloads disponíveis por 24 horas e animações breves na interface.
- Manuais revisados para instalação, uso, administração e backup.

## Instalação

Extraia o ZIP, execute `INSTALAR.cmd` e `CONFIGURAR_ADMIN.cmd`. Não existe senha padrão. Siga `LEIA_PRIMEIRO.md` para iniciar com a agenda parada e configurar banco e certificado.

## Atualização

Pare o app e faça backup do banco fiscal, `dados/admin_accounts.db` e `secrets`. Preserve certificado e logs. Atualize o código e execute `INSTALAR.cmd`. Contas existentes são preservadas; use `CONFIGURAR_ADMIN.cmd` para criar ou redefinir a senha administrativa.

A agenda salva em `secrets/auto-sync.json` prevalece sobre o ambiente. Para pausar, desative no Admin antes de encerrar o processo. Confira banco, certificado e agenda após reiniciar.

## Funcionamento

Consultas e relatórios usam os dados locais. O AtlasDFE não emite manifestações, cancelamentos ou CC-e. A marcação manual de cancelamento altera somente a situação interna. A captura SEFAZ usa Windows, PowerShell e certificado A1; o processo precisa permanecer aberto. A recuperação `consNSU` fica desativada por padrão.

O pacote não contém dados fiscais, senhas ou certificado. A publicação exige testes aprovados em Windows e Linux.
