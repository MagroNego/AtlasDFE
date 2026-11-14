# Atlas DFE na arquitetura do Oculus

A nova Consulta usa Next.js e TypeScript no frontend. O backend usa ASP.NET Core 10 com os projetos `Atlas.Api`, `Atlas.Domain` e `Atlas.Infrastructure`. A aplicação abre a mesma origem para a interface e a API, usa sessão Identity e grava suas tabelas no PostgreSQL com migrações EF Core.

A busca aceita número da NF-e ou chave de acesso. Ela mostra eventos, CC-e, vínculos CT-e e a situação de cancelamento da cópia local. A tela informa quando houve a última importação. Várias notas com o mesmo número aparecem como resultados separados.

O importador lê um snapshot consistente do SQLite ou SQLCipher e substitui, em uma transação, a cópia de Consulta daquele CNPJ no PostgreSQL. O comando em C# preserva os eventos, os vínculos CT-e, as CC-e e a situação de cancelamento da origem. A origem continua sendo o banco operacional, com seu próprio cursor, fila, respostas pendentes e agendamento. Sincronização SEFAZ, Emissão, Excel, importação operacional de XML e demais rotinas continuam no aplicativo Python nesta etapa.

## Executar localmente no Windows

Requer Docker Desktop e PowerShell 7. Execute na raiz do repositório.

```powershell
$env:ATLAS_CNPJ = Read-Host 'CNPJ com 14 dígitos'
if (-not (Test-Path secrets/postgres-password)) {
    [IO.File]::WriteAllText(
        (Join-Path (Get-Location) 'secrets/postgres-password'),
        [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
    )
}
docker compose up -d --build
$env:Bootstrap__Email = Read-Host 'E-mail de acesso'
$env:Bootstrap__Password = [Net.NetworkCredential]::new('', (Read-Host 'Senha de acesso' -AsSecureString)).Password
try { docker compose run --rm -e Bootstrap__Email -e Bootstrap__Password api --bootstrap-admin }
finally { Remove-Item Env:Bootstrap__Email, Env:Bootstrap__Password }
```

Abra [localhost:3001](http://localhost:3001). O administrador inicial é criado uma vez, com e-mail e senha de pelo menos 12 caracteres, incluindo maiúscula, minúscula, número e símbolo. Esse login pertence à nova Consulta.

## Importar uma cópia do histórico

O importador usa o SDK .NET 10 definido em `global.json`. Execute na raiz do repositório, com o PostgreSQL do Compose em execução. Use o mesmo `ATLAS_CNPJ` da configuração do Compose.

```powershell
$env:Atlas__Cnpj = $env:ATLAS_CNPJ
$senhaPg = (Get-Content -Raw secrets/postgres-password).Trim()
$portaPg = if ($env:ATLAS_DB_PORT) { $env:ATLAS_DB_PORT } else { '55432' }
$env:ConnectionStrings__Database = "Host=127.0.0.1;Port=$portaPg;Database=atlas;Username=atlas;Password=$senhaPg"
$env:Atlas__LegacyPassword = [Net.NetworkCredential]::new('', (Read-Host 'Senha do SQLCipher (Enter para SQLite)' -AsSecureString)).Password
try { dotnet run --project backend/Atlas.Api -- --importar-historico 'C:\caminho\nfe_manifestacoes_seguro.db' }
finally { Remove-Item Env:ConnectionStrings__Database, Env:Atlas__LegacyPassword -ErrorAction SilentlyContinue; Remove-Variable senhaPg }
```

O importador recebe a senha pela variável `Atlas__LegacyPassword` ou por um arquivo privado apontado por `Atlas__LegacyPasswordFile`. Para SQLite sem criptografia, deixe ambas ausentes. Pode ser executado de novo para atualizar a cópia. Para conferir a importação, pesquise uma NF-e com sequência de eventos conhecida, outra cancelada e um número que aparece em mais de uma série. Compare com a Consulta do Atlas atual. Vá para a migração das rotinas de escrita depois de validar a equivalência com a sua base.

O Compose desta etapa abre a interface e a porta de importação apenas em loopback. Para diagnosticar a inicialização, use `docker compose logs migrate api frontend`.

## Executar no CachyOS

Com o Docker e o Compose disponíveis, execute na raiz do repositório.

```bash
read -rp 'CNPJ com 14 dígitos: ' ATLAS_CNPJ
export ATLAS_CNPJ
if [ ! -f secrets/postgres-password ]; then
  (umask 077; openssl rand -hex 32 > secrets/postgres-password)
fi
# O diretório pai protege a senha no host. O serviço roda com usuário próprio.
chmod 700 secrets
chmod 444 secrets/postgres-password
docker compose up -d --build
read -rp 'E-mail de acesso: ' Bootstrap__Email
read -rsp 'Senha de acesso: ' Bootstrap__Password
printf '\n'
export Bootstrap__Email Bootstrap__Password
docker compose run --rm -e Bootstrap__Email -e Bootstrap__Password api --bootstrap-admin
unset Bootstrap__Email Bootstrap__Password
```

Para importar no CachyOS, instale o SDK .NET 10 indicado em `global.json` e execute:

```bash
read -rsp 'Senha do SQLCipher (Enter para SQLite): ' Atlas__LegacyPassword
printf '\n'
Atlas__Cnpj="$ATLAS_CNPJ" Atlas__LegacyPassword="$Atlas__LegacyPassword" \
ConnectionStrings__Database="Host=127.0.0.1;Port=${ATLAS_DB_PORT:-55432};Database=atlas;Username=atlas;Password=$(cat secrets/postgres-password)" \
  dotnet run --project backend/Atlas.Api -- --importar-historico /caminho/nfe_manifestacoes_seguro.db
unset Atlas__LegacyPassword
```

## Reprocessar respostas SEFAZ em ambiente de teste

A implementação em C# monta SOAP para `distNSU` e `consNSU`, lê os retornos compactados e valida os NSUs. O replay usa o arquivo bruto de uma consulta já realizada e as tabelas `sefaz_*` do PostgreSQL. Use um banco separado para validar a sequência histórica. Rode a migração antes do replay e configure `Atlas__Cnpj` e `ConnectionStrings__Database`.

```bash
dotnet run --project backend/Atlas.Api -- --migrate
dotnet run --project backend/Atlas.Api -- --reprocessar-sefaz distNSU 000000000000041 /caminho/resposta.xml
```

Informe o NSU enviado na requisição original. Para `consNSU`, carregue primeiro uma resposta `distNSU` que estabeleça o cursor. O replay guarda o XML bruto antes de validar, grava os documentos e o cursor juntos e ignora a reaplicação da mesma resposta. O processamento de `consNSU` e a rejeição 656 preservam o cursor. O replay preserva os documentos para verificação. Para consultar a SEFAZ com certificado e publicar na Consulta, use os comandos de sincronização manual.

O teste de protocolo roda com `dotnet run --project tests/Atlas.Sefaz.Checks`. Para executar também a integração, configure `ATLAS_TEST_DATABASE` apontando para um PostgreSQL de teste vazio cujo nome comece por `atlas_test_` e aplique as migrações nele antes de executar.

## Sincronização manual em C#

O comando processa um lote por execução. Ele retoma primeiro uma resposta pendente, respeita a janela configurada em horário de Brasília e consulta o PostgreSQL para verificar a pausa e o limite de recuperação. Novas NF-e, manifestações, CC-e e cancelamentos reconhecidos aparecem na Consulta. Os documentos completos ficam gravados no PostgreSQL.

Para a primeira execução, importe o histórico, pare a coleta Python do CNPJ e informe o cursor atual do Atlas antigo em `Sefaz__InitialNsu`. Depois, use o cursor persistido removendo essa variável. Configure `Sefaz__Uf` com o código da UF, como `33` para o RJ. O padrão da janela é 22:00 a 04:00. Ajuste com `Sefaz__WindowStart` e `Sefaz__WindowEnd`.

Para A1, informe `Sefaz__CertificatePath` e `Sefaz__CertificatePasswordFile`, com o caminho de um arquivo privado contendo a senha. No Windows, também é possível informar `Sefaz__CertificateThumbprint` de um certificado no repositório Pessoal do usuário que executa o comando. Valide o acesso à chave privada e o dispositivo no ambiente da TI.

```bash
dotnet run --project backend/Atlas.Api -- --migrate
dotnet run --project backend/Atlas.Api -- --sincronizar-sefaz
dotnet run --project backend/Atlas.Api -- --recuperar-nsu 000000000000042
```

A recuperação manual aceita um NSU por comando e preserva o cursor de distribuição. A fila prioritária e o agendamento são configurados na próxima seção. A atualização da cópia histórica deve ocorrer antes da primeira tentativa de sincronização C#.

## Fila prioritária e agenda em C#

Depois de aplicar a migração e estabelecer o cursor pelo comando manual, use os comandos abaixo com o mesmo CNPJ e PostgreSQL:

```bash
dotnet run --project backend/Atlas.Api -- --enfileirar-nsu 000000000000042 alta
dotnet run --project backend/Atlas.Api -- --fila-nsu
dotnet run --project backend/Atlas.Api -- --cancelar-nsu 000000000000042
```

A fila aceita prioridade `alta` ou `normal`, com limite de 100 pendências. Cada horário agendado processa um NSU pendente elegível, começando pela maior prioridade. Quando a fila não tem item elegível, o agendador usa a distribuição. A aplicação grava a reserva de cada horário no PostgreSQL para evitar uma segunda chamada após reinício. Um item com retorno 137 fica indisponível, e um item recebido fica concluído mesmo se chegou pela distribuição.

Para ativar, inicie a API com `Sefaz__ScheduleEnabled=true`, as variáveis do certificado e da UF e o mesmo PostgreSQL. `Sefaz__ScheduleTimes` permite alterar a lista de horários, e `Sefaz__ScheduleDays` usa 0 para domingo até 6 para sábado. Por padrão a agenda fica desabilitada. Se usar Compose, configure essas variáveis e monte o certificado no serviço da API. Um certificado que depende do repositório do Windows exige execução no Windows.

A fila deste incremento recebe NSUs explicitamente cadastrados. A importação das pendências do SQLite e a descoberta automática de lacunas ainda estão pendentes. A Consulta exibe também os vínculos CT-e reconhecidos nos lotes recebidos e mantém o cancelamento de uma chave de CT-e já identificada.
