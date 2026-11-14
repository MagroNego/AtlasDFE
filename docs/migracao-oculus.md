# Atlas DFE na arquitetura do Oculus

A nova Consulta usa Next.js e TypeScript no frontend. O backend usa ASP.NET Core 10 com os projetos `Atlas.Api`, `Atlas.Domain` e `Atlas.Infrastructure`. A aplicação abre a mesma origem para a interface e a API, usa sessão Identity e grava suas tabelas no PostgreSQL com migrações EF Core.

A busca aceita número da NF-e ou chave de acesso. Ela mostra eventos, CC-e, vínculos CT-e e a situação de cancelamento da cópia local. A tela informa quando houve a última importação. Várias notas com o mesmo número aparecem como resultados separados.

O importador lê um snapshot consistente do SQLite ou SQLCipher e substitui, em uma transação, a cópia de Consulta daquele CNPJ no PostgreSQL. A operação usa o mesmo leitor de eventos e vínculos do Atlas atual. A origem continua sendo o banco operacional, com seu próprio cursor, fila, respostas pendentes e agendamento. Sincronização SEFAZ, Emissão, Excel, importação operacional de XML e demais rotinas continuam no aplicativo Python nesta etapa.

## Executar localmente no Windows

Requer Docker Desktop, PowerShell 7 e o ambiente Python usado pelo Atlas atual. Execute na raiz do repositório.

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

No Python do Atlas, instale o extra uma vez com `python -m pip install -e ".[postgres]"`. A opção `--cnpj` deve usar o mesmo CNPJ configurado no Compose.

```powershell
$senhaPg = (Get-Content -Raw secrets/postgres-password).Trim()
$portaPg = if ($env:ATLAS_DB_PORT) { $env:ATLAS_DB_PORT } else { '55432' }
$env:ATLAS_POSTGRES_DSN = "host=127.0.0.1 port=$portaPg dbname=atlas user=atlas password=$senhaPg"
try { python -m nfe_consulta.postgres_snapshot 'C:\caminho\nfe_manifestacoes_seguro.db' --cnpj $env:ATLAS_CNPJ }
finally { Remove-Item Env:ATLAS_POSTGRES_DSN; Remove-Variable senhaPg }
```

O comando pede a senha do SQLCipher quando necessário. Pode ser executado de novo para atualizar a cópia. Para conferir a importação, pesquise uma NF-e com sequência de eventos conhecida, outra cancelada e um número que aparece em mais de uma série. Compare com a Consulta do Atlas atual. Vá para a migração das rotinas de escrita depois de validar a equivalência com a sua base.

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

Para importar, use o mesmo comando Python apontando para o arquivo fiscal no Linux. Defina a variável de conexão no terminal em que rodará o importador.

```bash
ATLAS_POSTGRES_DSN="host=127.0.0.1 port=${ATLAS_DB_PORT:-55432} dbname=atlas user=atlas password=$(cat secrets/postgres-password)" \\
  python -m nfe_consulta.postgres_snapshot /caminho/nfe_manifestacoes_seguro.db --cnpj "$ATLAS_CNPJ"
```
