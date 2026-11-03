# Instalação e atualização

## Requisitos

| Requisito | Especificação |
| --- | --- |
| Produção com SEFAZ | Windows 10/11 ou Windows Server; PowerShell disponível |
| Python | 3.11+; versão usada na CI: 3.12 |
| Instalação Windows | Python Launcher `py` e acesso ao índice de dependências Python |
| Banco | Banco de manifestações existente e compatível, SQLite/SQLCipher |
| Certificado | A1 válido, PFX/P12 ou Windows Certificate Store; chave privada acessível ao processo |
| Rede | HTTPS para o endpoint SEFAZ; SMTP se houver alertas |
| Permissões | Leitura do certificado e escrita em banco, `dados`, `secrets` e `logs` |

A instalação não fornece certificado, senha, banco fiscal preenchido ou credenciais SMTP. O CNPJ e a UF padrão desta distribuição estão em `nfe_consulta/config.py`; valide-os antes de usar o app para outra empresa. O banco selecionado deve conter a tabela `manifestacoes`; um arquivo SQLite vazio não atende à configuração administrativa.

## Código e versão

Esta documentação acompanha a versão **2.7.0**. Baixe o ZIP em [Releases](https://github.com/MagroNego/ConsultaManifestacao_DFE/releases/latest) ou obtenha o código por Git. O ZIP inclui o instalador, lançadores e manuais; não inclui banco fiscal, certificado ou credenciais.

Para instalar pelo ZIP, extraia em uma pasta definitiva, execute `INSTALAR.cmd`, depois `CONFIGURAR_ADMIN.cmd`. No PowerShell aberto nessa pasta, execute `$env:NFE_AUTO_SYNC_ENABLED = "0"` e `.\INICIAR_WEB.cmd`. Continue em **Configuração inicial**, abaixo.

## Windows: instalação por Git

Use uma pasta definitiva com permissão de escrita. O diretório `C:\AtlasDFE` deve estar ausente ou vazio para o clone. No PowerShell:

```powershell
git clone --branch main https://github.com/MagroNego/ConsultaManifestacao_DFE.git C:\AtlasDFE
Set-Location C:\AtlasDFE
git rev-parse HEAD
py --version
.\INSTALAR.cmd
.\CONFIGURAR_ADMIN.cmd
$env:NFE_AUTO_SYNC_ENABLED = "0"
$env:NFE_WEB_LOCAL_AUTH = "0"
.\INICIAR_WEB.cmd
```

`INSTALAR.cmd` cria `.venv`, instala as dependências e prepara `dados`, `secrets` e `logs`. Consultas e relatórios não exigem conta. Admin, importações e alterações manuais da situação de notas exigem login administrativo. `CONFIGURAR_ADMIN.cmd` pede uma senha de pelo menos 12 caracteres e grava somente seu hash no banco de contas; não existe senha padrão.

O lançador inicia em produção, gera `secrets/web-csrf-secret.txt` se necessário e ativa a sincronização automática quando a variável não foi definida. Os comandos de instalação acima desativam os disparos iniciais. A variável abaixo só controla a ativação quando não existe agenda salva:

```powershell
$env:NFE_AUTO_SYNC_ENABLED = "0"
.\INICIAR_WEB.cmd
```

Se `secrets/auto-sync.json` já existir, seu campo `enabled` prevalece sobre a variável. Desative a agenda no Admin ou, com o processo parado, altere esse campo para `false`, preservando `times`, `weekdays`, `window_start` e `window_end`. Não remova o arquivo para pausar a agenda.

## Configuração inicial

1. Execute `CONFIGURAR_ADMIN.cmd` se ainda não definiu a senha. Acesse `http://127.0.0.1:8080`, abra **Admin** e entre com as credenciais configuradas.
2. Em **Admin**, selecione o banco fiscal existente. O caminho padrão é `dados/nfe_manifestacoes_seguro.db`; a seleção salva em `secrets/db-path.txt` prevalece.
3. Para SQLCipher, disponibilize a senha em `secrets/db-password.txt`. Essa credencial pertence ao banco, não ao acesso Web.
4. Configure o certificado A1 por arquivo ou repositório Windows. O arquivo PFX/P12 permanece no caminho selecionado; não é copiado para o projeto.
5. Confira banco, última gravação, `ultNSU` e `maxNSU` em **Status**.
6. Se houver alertas, configure SMTP e destinatários e use **Enviar e-mail de teste**.
7. Ajuste a janela do AtlasDFE em **Admin → Disparos automáticos** para separar sua captura da do Qive. A referência usada no projeto é AtlasDFE 22h–04h e Qive 08h–18h. Consulte [Distribuição e Qive](DISTRIBUICAO_DFE_QIVE.md).

Para ativar a rotina, use **Admin → Disparos automáticos** e salve a configuração. Em instalações sem configuração salva, também é possível definir a ativação inicial pelo ambiente e reiniciar:

```powershell
$env:NFE_AUTO_SYNC_ENABLED = "1"
$env:NFE_RECOVERY_ENABLED = "0"
.\INICIAR_WEB.cmd
```

Após salvar pelo Admin, `secrets/auto-sync.json` prevalece sobre os padrões de ativação/dias do ambiente; preserve-o nas atualizações e backups.

As variáveis acima valem para esse PowerShell e os processos iniciados por ele. Para serviço Windows, configure-as no ambiente do serviço. `WEB_CONFIG.example` é uma referência e **não é carregado automaticamente**.

## Janela e disponibilidade

A janela padrão é **22:00 inclusive até 04:00 exclusiva, Brasília (UTC−3)**. Os disparos ocorrem às **22h, 23h, 00h, 01h, 02h e 03h**, todos os dias por padrão. Consultas manuais e retentativas também respeitam janela, trava e cooldown de 60 minutos. No padrão, não há disparo às 04h. Início e fim da janela são configuráveis no Admin, junto com os disparos.

Mantenha o computador sem suspensão/hibernação e o app em execução. Fechar o terminal encerra a aplicação. O agendador não liga o computador nem inicia sozinho um processo encerrado. Use um serviço do sistema operacional para inicialização automática, conforme [Servidor Web](SERVIDOR_WEB.md).

## Rede interna

O lançador padrão escuta somente em `127.0.0.1:8080`. Para produção na rede, publique por HTTPS em um proxy reverso, mantendo o backend acessível somente ao proxy. `INICIAR_REDE.bat`, disponível no repositório, usa `0.0.0.0:8080` e mantém a senha apenas nas funções administrativas; consultas ficam acessíveis a quem alcançar a porta. O ZIP gerado pelo empacotador não inclui esse BAT. Veja [Servidor Web](SERVIDOR_WEB.md).

## Linux: consultas locais e desenvolvimento

A interface, importação e relatórios locais executam em Linux. O cliente SEFAZ e a leitura de A1 dependem do executável PowerShell usado pelo código; não considere a sincronização Linux homologada por este procedimento. Mantenha-a desabilitada.

Instale Python com suporte a `venv`, compilador e bibliotecas de desenvolvimento SQLCipher exigidas pelo pacote `sqlcipher3`. Na pasta do projeto:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
mkdir -p secrets dados logs
chmod 700 secrets dados logs
.venv/bin/python -c "from pathlib import Path; import secrets; p=Path('secrets/web-csrf-secret.txt'); p.exists() or p.write_text(secrets.token_urlsafe(48), encoding='utf-8')"
chmod 600 secrets/*
.venv/bin/python -m nfe_consulta.web.configure_admin
env NFE_WEB_ENV=production NFE_AUTO_SYNC_ENABLED=0 NFE_RECOVERY_ENABLED=0 .venv/bin/python -m nfe_consulta.web.app
```

## Atualização por Git

Pare o app e faça o [backup](SEGURANCA_BANCO.md). Na pasta do repositório:

```powershell
git status --short
git branch --show-current
git switch main
git pull --ff-only origin main
.\INSTALAR.cmd
.\INICIAR_WEB.cmd
```

Se houver alterações locais ou divergência, resolva-as antes de atualizar; não use `reset --hard` para descartar arquivos da instalação. O `git pull` atualiza código, não o processo já aberto.

## Atualização por ZIP

Use um pacote identificado por versão/commit e confira o checksum quando fornecido. Rebaixar para um ZIP anterior pode restaurar comportamentos antigos. Arquivo de código baixado do GitHub não é o mesmo artefato que o ZIP montado por `scripts/package_release.py`.

1. Pare o app e faça backup do banco fiscal, inclusive quando estiver fora da pasta do projeto.
2. Extraia o pacote novo em pasta temporária.
3. Substitua `nfe_consulta` por inteiro e atualize `pyproject.toml`, lançadores, configuração de exemplo e documentação.
4. Preserve `dados`, `secrets`, `logs`, certificado, entradas e saídas. Preserve `.venv` e atualize suas dependências pelo instalador.
5. Execute `INSTALAR.cmd` e reinicie com `INICIAR_WEB.cmd`.

Em ambos os métodos, confira acesso, versão/commit, banco, certificado, agenda salva e cursores. Use `Ctrl+F5` no navegador para recarregar os recursos da interface. Não zere o NSU nem apague pendências para validar a atualização. Remova lançadores de teste antigos e variáveis de agendamento legadas descritas em [Servidor Web](SERVIDOR_WEB.md).

## Diagnóstico de instalação

| Sintoma | Verificação |
| --- | --- |
| `py` não encontrado | Instalar Python Launcher; reabrir o terminal |
| Falha ao instalar SQLCipher | Conferir a saída do pip e as dependências nativas da plataforma |
| Banco indisponível | Caminho selecionado, permissões e senha SQLCipher |
| Certificado indisponível | Validade, chave privada, senha e conta que executa o processo |
| Token de segurança inválido | Recarregar a página para obter um token atual; repetir a ação sem reutilizar formulário antigo |
| Sincronização não inicia | Horário Brasília, processo ativo, agenda efetiva salva e próxima tentativa em Status |
| Porta ocupada | Verificar outra instância antes de mudar a porta; manter um único processo |
