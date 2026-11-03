# AtlasDFE

Aplicação Web interna para consulta de manifestações de NF-e, importação de dados de XML e geração de relatórios Excel/CSV. Desenvolvida em Python, FastAPI e SQLite/SQLCipher. A captura de eventos usa `NFeDistribuicaoDFe`; consultas e relatórios usam o banco local.

Versão **2.7.0**. [Baixar a versão estável](https://github.com/MagroNego/ConsultaManifestacao_DFE/releases/latest).

Consultas e relatórios são livres. Admin, importações e alterações manuais de situação exigem senha.

## Operação

| Item | Configuração |
| --- | --- |
| Runtime | Python 3.11+; CI com Python 3.12 |
| Servidor | FastAPI/Uvicorn; um processo, um worker |
| Banco fiscal | SQLite ou SQLCipher; caminho selecionado em Admin |
| Acesso | Consultas e relatórios sem login; Admin e cancelamento manual exigem senha |
| Sincronização SEFAZ | Windows com PowerShell e certificado A1 |
| Janela padrão AtlasDFE | **22:00 inclusive até 04:00 exclusiva, Brasília (UTC−3)** |
| Disparos automáticos | **22:00, 23:00, 00:00, 01:00, 02:00 e 03:00** por padrão; configuráveis no Admin |
| Janela solicitada ao Qive | **08:00–18:00**, incluindo novas tentativas |
| Recuperação `consNSU` | Pausada por padrão: `NFE_RECOVERY_ENABLED=0` |
| Alertas de e-mail | Somente Operação não Realizada (`210240`) |
| Retenção | Downloads: 24 horas; histórico de sincronização: 30 dias |

O login corporativo é uma opção de integração com gateway autenticado, com grupos de Consulta e Administrador. Permanece desativado até a configuração: [contrato de integração](SERVIDOR_WEB.md#preparação-para-login-corporativo).

Em **Admin → Disparos automáticos**, ative/desative a agenda e escolha a janela, horários e dias sem reiniciar. São aceitos até seis horários dentro da janela, separados por pelo menos 60 minutos. A configuração salva prevalece sobre a ativação inicial do lançador.

A interface e os testes locais também executam em Linux. O transporte SEFAZ e a leitura de certificados usam PowerShell; a implantação documentada para sincronização é Windows. A aprovação da CI Linux não valida comunicação real com a SEFAZ.

## Instalação Windows

Para instalar o estado descrito nesta documentação, obtenha a `main` por Git e execute na pasta do projeto:

```powershell
git clone --branch main https://github.com/MagroNego/ConsultaManifestacao_DFE.git
Set-Location ConsultaManifestacao_DFE
.\INSTALAR.cmd
.\CONFIGURAR_ADMIN.cmd
$env:NFE_AUTO_SYNC_ENABLED = "0"
.\INICIAR_WEB.cmd
```

O comando acima desativa a agenda inicial em uma instalação nova. Uma agenda já salva em `secrets/auto-sync.json` prevalece.

Acesse `http://127.0.0.1:8080`. Abra Admin e configure o banco de manifestações existente e o certificado A1. As credenciais, o certificado e o banco fiscal não acompanham o pacote. Configure a conta em `CONFIGURAR_ADMIN.cmd` e entre no Admin com essa senha. Consultas e relatórios continuam livres; importações, configurações e alterações manuais da situação de notas exigem Admin. Procedimento completo: [Instalação](MANUAL_DE_INSTALACAO.md).

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

O pacote de cada release corresponde ao código da sua tag e inclui checksum SHA-256.

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

### Relatórios e CC-e

A exportação de Emissão inclui **Resumo**, Notas, Itens e Retencoes. A aba Resumo traz dados por item, hora de emissão e pesos da nota. Reimportar XMLs antigos completa esses metadados ausentes.

CC-e processadas podem ser importadas e consultadas pelas ações da nota em Consulta e Emissão. O texto fica no histórico do evento; não altera automaticamente os dados da nota.

Procedimentos completos: [Resumo](MANUAL_DE_USO.md#aba-resumo-na-exportação-de-emissão) e [Carta de Correção](MANUAL_DE_USO.md#carta-de-correção-cc-e).
