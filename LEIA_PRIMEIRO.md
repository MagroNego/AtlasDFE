# AtlasDFE — início rápido

## Implantação

1. Instale Python 3.11+ com Python Launcher (`py`). Python 3.12 é a versão usada na CI.
2. Obtenha o código da `main` por Git, conforme [Instalação](MANUAL_DE_INSTALACAO.md). Registre o commit instalado.
3. Execute `INSTALAR.cmd` e depois `CONFIGURAR_ADMIN.cmd` para definir a senha administrativa.
4. Na primeira instalação, defina `$env:NFE_AUTO_SYNC_ENABLED = "0"` no PowerShell antes de executar `INICIAR_WEB.cmd`.
5. Acesse `http://127.0.0.1:8080`, abra **Admin** e configure banco fiscal existente, senha SQLCipher e certificado A1.
6. Confira **Status**, configure os alertas se necessários e habilite a agenda somente após validar a instalação.

O acesso comum dispensa login. Admin, importações e cancelamento manual de notas exigem a senha administrativa. Não há senha padrão: configure-a com `CONFIGURAR_ADMIN.cmd`. Para acesso pela rede, publique o app por HTTPS e restrinja a porta do backend ao proxy. O programa não instala proxy, autenticação corporativa ou serviço Windows.

Em instalação existente, `secrets/auto-sync.json` prevalece sobre a variável de ativação. Para manter a agenda parada durante manutenção, siga [Backup e restauração](SEGURANCA_BANCO.md).

## Operação

- Janela padrão do AtlasDFE: **22:00 inclusive até 04:00 exclusiva, Brasília (UTC−3)**.
- Agenda padrão: **22h, 23h, 00h, 01h, 02h e 03h**. Janela, horários e dias podem ser alterados no Admin.
- Qive: janela solicitada **08h–18h**, incluindo retentativas. O AtlasDFE não altera a agenda do Qive.
- `consNSU`: desativado por padrão; pendências preservadas.
- Mantenha o computador sem suspensão/hibernação e o processo ativo. `Win+L` não encerra a sessão; fechar o terminal encerra o processo.
- Consultas locais, importações e relatórios funcionam fora da janela SEFAZ.

## Atualização

Pare o processo, faça backup, atualize o código e reinstale as dependências. Preserve banco, `secrets`, certificados e logs. Reinicie e confira acesso, banco, certificado, agenda e cursores. A versão `2.6.0` isolada não identifica as correções instaladas; registre também o commit.

[Instalação](MANUAL_DE_INSTALACAO.md) · [Uso](MANUAL_DE_USO.md) · [Servidor](SERVIDOR_WEB.md) · [Distribuição e Qive](DISTRIBUICAO_DFE_QIVE.md) · [Backup](SEGURANCA_BANCO.md)
