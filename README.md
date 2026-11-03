# AtlasDFE

Aplicação Web interna para consulta de manifestações de NF-e, importação de dados de XML e geração de relatórios Excel/CSV. Desenvolvida em Python, FastAPI e SQLite/SQLCipher. A captura de eventos usa `NFeDistribuicaoDFe`; consultas e relatórios usam o banco local.

Documentação revisada em **19/10/2026** para o código da `main`, versão declarada **2.6.0**. O ZIP da release `v2.6.0` foi publicado antes das últimas alterações e não é atualizado automaticamente. Identifique a instalação pelo commit, além do número da versão.

## Operação

| Item | Configuração |
| --- | --- |
| Runtime | Python 3.11+; CI com Python 3.12 |
| Servidor | FastAPI/Uvicorn; um processo, um worker |
| Banco fiscal | SQLite ou SQLCipher; caminho selecionado em Admin |
| Acesso | Sem login local; controle de acesso sob responsabilidade do TI |
| Sincronização SEFAZ | Windows com PowerShell e certificado A1 |
| Janela AtlasDFE | **22:00 inclusive até 04:00 exclusiva, Brasília (UTC−3)** |
| Disparos automáticos | **22:00, 23:00, 00:00, 01:00, 02:00 e 03:00** por padrão; configuráveis no Admin |
| Janela solicitada ao Qive | **08:00–18:00**, incluindo novas tentativas |
| Recuperação `consNSU` | Pausada por padrão: `NFE_RECOVERY_ENABLED=0` |
| Alertas de e-mail | Somente Operação não Realizada (`210240`) |
| Retenção | Downloads: 24 horas; histórico de sincronização: 30 dias |

O login corporativo pode ser habilitado pelo TI por um gateway autenticado, com grupos de Consulta e Administrador. Permanece desativado até a configuração: [contrato de integração](SERVIDOR_WEB.md#preparação-para-login-corporativo).

Em **Admin → Disparos automáticos**, ative/desative a agenda e escolha horários e dias sem reiniciar. São aceitos até seis horários dentro da janela, separados por pelo menos 60 minutos. A configuração salva prevalece sobre a ativação inicial do lançador.

A interface e os testes locais também executam em Linux. O transporte SEFAZ e a leitura de certificados usam PowerShell; a implantação documentada para sincronização é Windows. A aprovação da CI Linux não valida comunicação real com a SEFAZ.

## Instalação Windows

Para instalar o estado descrito nesta documentação, obtenha a `main` por Git e execute na pasta do projeto:

```powershell
git clone --branch main https://github.com/MagroNego/ConsultaManifestacao_DFE.git
Set-Location ConsultaManifestacao_DFE
.\INSTALAR.cmd
$env:NFE_AUTO_SYNC_ENABLED = "0"
.\INICIAR_WEB.cmd
```

O comando acima desativa a agenda inicial em uma instalação nova. Uma agenda já salva em `secrets/auto-sync.json` prevalece.

Acesse `http://127.0.0.1:8080`. Abra Admin e configure o banco de manifestações existente e o certificado A1. As credenciais, o certificado e o banco fiscal não acompanham o pacote. O Admin também abre sem senha: a restrição de acesso deve existir na infraestrutura do TI. Procedimento completo: [Instalação](MANUAL_DE_INSTALACAO.md).

## Recursos

- Consulta de manifestações por período do evento, nota, chave, tipo e situação.
- Emissão: notas importadas, itens, impostos, retenções e cancelamentos registrados.
- Consulta por TXT de chaves ou números de notas, exportação de todas as páginas filtradas e Downloads.
- Vínculos de CT-e recebidos por eventos, com os campos efetivamente disponíveis.
- Sincronização automática e manual administrativa, cursor NSU e diagnóstico de falhas.
- Remetente SMTP configurável e e-mail de teste.

Consultas, importações e relatórios trabalham no banco local. O AtlasDFE não transmite manifestações ou cancelamentos. Novos XMLs são processados e os dados necessários ficam no banco; o original deve ser obtido no Synchro. Respostas SEFAZ pendentes de processamento são preservadas temporariamente.

O app não consulta a situação fiscal atual de cada nota. O rótulo **Autorizada** significa ausência de cancelamento no banco local. Cancelamentos de emissão própria precisam de uma fonte adicional, como o XML do evento; o lote de XMLs originais não comprova a situação atual. Veja [Situação e CT-e](MANUAL_DE_USO.md#situação-e-ct-e).

## Distribuição e Qive

O suporte confirmou o uso de `distNSU` pelo Qive e aceitou solicitar a configuração das 08:00 às 18:00. O AtlasDFE aplica sua própria janela no backend, inclusive às consultas manuais, recuperações e retentativas. Uma requisição já enviada pode terminar após 04:00; não se inicia outra fora da janela.

Os detalhes do atendimento, o comportamento do cursor, os resultados das duas noites de teste e a alternativa comercial Gateway estão em [Distribuição DF-e e Qive](DISTRIBUICAO_DFE_QIVE.md). Horários separados não são documentados como garantia de ausência de rejeição 656.

## Documentação

| Documento | Conteúdo |
| --- | --- |
| [Leia primeiro](LEIA_PRIMEIRO.md) | Sequência inicial |
| [Instalação](MANUAL_DE_INSTALACAO.md) | Requisitos, instalação, configuração e atualização |
| [Uso](MANUAL_DE_USO.md) | Telas, relatórios, importação e diagnóstico |
| [Distribuição DF-e e Qive](DISTRIBUICAO_DFE_QIVE.md) | Janelas, NSU, 656, consNSU e informações do suporte |
| [Servidor Web](SERVIDOR_WEB.md) | Processo, rede, variáveis e SMTP |
| [Banco e backup](SEGURANCA_BANCO.md) | Arquivos persistentes, backup e restauração |
| [Arquitetura](ARQUITETURA.md) | Componentes, transações e limites |
| [Histórico](CHANGELOG.md) | Alterações por versão |

## Desenvolvimento e pacote

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m compileall -q nfe_consulta
python scripts/package_release.py
```

A CI testa Windows e Linux com dados sintéticos, sem chamadas reais à SEFAZ nem envio real de e-mail. O pacote contém código, recursos, lançadores e documentação; não contém dados operacionais.

A versão declarada permanece `2.6.0`. A branch `main` pode conter correções posteriores ao ZIP dessa release. O workflow não substitui uma release existente: um pacote novo exige atualização de versão e nova publicação.

### Janela de consultas configurável

No **Admin → Disparos automáticos**, informe o início e o fim da janela em
horário de Brasília e os horários dos disparos. O início é incluído e o fim
é excluído; por exemplo, 22:00–04:00 permite consultar antes das 04:00.
O padrão continua 22:00–04:00. Evite sobreposição com a janela combinada com
outros coletores, como o Qive.

Salve a janela e os disparos juntos. Os disparos precisam estar dentro da
janela e respeitar 60 minutos entre execuções nos dias selecionados.
A configuração vale sem reiniciar e permanece após reinício. Salvar não
consulta a SEFAZ nem altera o NSU. Cooldown e bloqueios continuam valendo.
Consultas e exportações locais permanecem disponíveis fora da janela.

### Aba Resumo na exportação de Emissão

A exportação geral em Excel inclui a aba **Resumo**, além de Notas, Itens e
Retencoes. Ela contém número da nota, código do produto, quantidade, valor
total do item, CFOP, data e hora de emissão, peso líquido, peso bruto e status.
Os pesos somam os volumes informados na NF-e e se repetem por item; não são
pesos individuais nem rateados. Campos ausentes ficam em branco, distintos de zero.

Para notas importadas antes dessa versão, reimporte os XMLs para completar
hora e pesos que ainda não estavam armazenados. A reimportação complementa
somente esses metadados ausentes, preservando os dados fiscais e os cancelamentos.

### Carta de Correção (CC-e)

Em **Emissão → Importar XML**, envie o XML processado da CC-e junto com os XMLs
das notas, ou no mesmo ZIP. A ordem dos arquivos não importa. O evento precisa
conter a solicitação e o retorno da SEFAZ (`procEventoNFe`, código `110110`,
status `135`), do ambiente de produção e do CNPJ emitente configurado.

A CC-e fica vinculada pela chave, mesmo se a nota for importada depois.
Reimportações não duplicam o evento. Em **Consulta**, filtre por **Carta de
Correção** e clique no ícone de documento com lápis ao lado do número da nota
para consultar data, hora, texto, sequência e protocolo de todas as cartas da nota.
A CC-e não cria uma linha extra na consulta; o filtro de Carta de Correção
localiza as notas com esse evento. A exportação de eventos mantém o histórico
completo, incluindo sequência e texto das cartas.

O texto é mantido como evento: não modifica automaticamente itens, valores,
pesos nem a situação de cancelamento da nota. A importação usa o banco local,
sem consultar a SEFAZ ou avançar o NSU. O AtlasDFE não emite CC-e.
