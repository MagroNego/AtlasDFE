# Atlas DFE

Consulta e operação fiscal de NF-e em ASP.NET Core, PostgreSQL e Next.js. A aplicação importa o histórico do Atlas anterior, sincroniza DF-e e mantém a fila de recuperação NSU. Inclui Emissão, importação XML/ZIP, relatórios Excel, Aging List e administração de contas.

## Instalação

Use Docker Desktop no Windows ou Docker Engine com Compose v2 no Linux. Configure o CNPJ e os segredos conforme o [guia de instalação e migração](docs/migracao-oculus.md). A interface local fica em [localhost:3001](http://localhost:3001).

A coleta automática começa desativada. Para a virada, importe a base final do Atlas anterior, confirme o cursor e teste a comunicação com a SEFAZ usando o certificado da instalação.

## Desenvolvimento

Requisitos: SDK .NET 10 indicado em `global.json`, Node.js 24 e PostgreSQL 18. Execute na raiz do repositório:

```bash
dotnet restore --locked-mode
dotnet build --no-restore
dotnet run --project backend/Atlas.Api -- --migrate
dotnet run --project backend/Atlas.Api -- --urls http://127.0.0.1:5080
```

Em outro terminal, execute `npm ci` e `npm run dev` em `frontend`. A API usa `ConnectionStrings__Database` e `Atlas__Cnpj` definidos no ambiente. Os comandos de teste e as opções de operação estão no guia.
