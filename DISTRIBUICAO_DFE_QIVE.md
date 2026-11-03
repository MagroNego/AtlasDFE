# Distribuição DF-e e convivência com Qive

Revisão: 03/11/2026. Escopo: configuração do AtlasDFE, atendimento Qive transcrito pelo operador e resultados apresentados no Status. O AtlasDFE não acessa a configuração interna do Qive.

## Janelas operacionais

Todos os horários desta seção são de Brasília (UTC−3).

| Sistema | Janela | Situação |
| --- | --- | --- |
| AtlasDFE | **22:00 inclusive até 04:00 exclusiva** | Padrão; configurável no Admin e aplicado no backend |
| Qive | **08:00 às 18:00**, incluindo novas tentativas | Janela solicitada ao suporte; configuração externa ao AtlasDFE |
| Margem entre sistemas | 04:00–08:00 e 18:00–22:00 | Quatro horas de separação em cada transição, se o Qive aplicar a janela solicitada |

A tabela registra a divisão de horários usada no projeto. O AtlasDFE aplica apenas sua própria janela; não lê nem altera a configuração do Qive.

O AtlasDFE dispara às **22:00, 23:00, 00:00, 01:00, 02:00 e 03:00**, diariamente por padrão. O Admin pode ativar/desativar e selecionar outros horários/dias sem reiniciar; início e fim da janela também são configuráveis e o intervalo mínimo entre horários é de 60 minutos. No padrão, às 04:00 a janela fecha, sem novo disparo. A restrição alcança sincronização manual, automática, recuperação e novas tentativas, inclusive entre chamadas. Uma requisição já enviada pode terminar após o fechamento; a próxima fica bloqueada. Consultas locais, importações e exportações não dependem dessa janela.

## Informações fornecidas pelo suporte Qive

| Tema | Informação do atendimento | Aplicação no projeto |
| --- | --- | --- |
| Captura | Confirmou uso do grupo `distNSU` no serviço `NFeDistribuicaoDFe` | Ambos os sistemas usam distribuição para o mesmo CNPJ |
| Horários | Ofereceu configuração de janela e afirmou que consultas e retentativas ficam restritas a ela | Foi solicitada a janela Qive 08h–18h |
| Margem | Sugeriu cerca de dez minutos de segurança no exemplo anterior, encerrado às 04h | Esse exemplo não é o horário final solicitado; o plano atual deixa quatro horas entre sistemas |
| Cursor | Orientou manter o `ultNSU` próprio de cada sistema e não gravar o cursor de uma rejeição como histórico recebido | O AtlasDFE preserva o último cursor processado com sucesso |
| Reconsulta | Afirmou que retomar NSU anterior em outra janela não causaria 656, citando disponibilidade de até 90 dias | Informação do fornecedor; não é garantia técnica adotada pelo AtlasDFE |
| Manifestações | Informou que a manifestação é enviada no momento da ação, sem aguardar a próxima captura | Refere-se ao Qive; o AtlasDFE não envia manifestações |
| Gateway | Ofereceu captura centralizada, compatibilidade `distNSU`/`consNSU` e distribuição de eventos, incluindo Operação não Realizada | Alternativa informada pelo fornecedor; não integrada nem testada neste projeto |
| Plano | Informou que o Gateway não está incluído no plano consultado | Depende de proposta comercial e habilitação |

A explicação inicial do suporte associou a captura à identificação das notas, Ciência da Emissão e obtenção de XMLs. O AtlasDFE não reproduz esse fluxo: sua sincronização lê os documentos distribuídos e não gera Ciência da Operação.

## Sequência NSU e rejeição 656

`distNSU` distribui documentos a partir do cursor informado. `consNSU` consulta um NSU específico. O NSU identifica um documento distribuído, que pode ser nota, resumo ou evento; não corresponde necessariamente ao número de uma nota ou à ordem de emissão.

A NT 2014.002 inclui consulta fora da sequência entre os critérios de consumo indevido e prevê retorno do último NSU em rejeições 656 de `distNSU`. Portanto, a afirmação do suporte de que apenas simultaneidade ou intervalo insuficiente explicam a rejeição não deve ser tratada como regra geral. Separar horários reduz sobreposição, mas não cria sequências independentes nem autoriza retroceder o cursor arbitrariamente. A disponibilidade histórica citada pelo suporte também não elimina controles de consumo.

Referência oficial: [NT 2014.002 — distribuição DF-e](https://www.nfe.fazenda.gov.br/Portal/exibirArquivo.aspx?conteudo=VgPuhtPApQo%3D), seção de uso indevido. O endereço identifica o documento consultado nesta revisão; verifique atualizações no Portal Nacional da NF-e.

## Comportamento implementado

1. Processar respostas `distNSU` recebidas e ainda pendentes antes de fazer nova chamada externa.
2. Enviar o último `ultNSU` efetivamente gravado; não substituir por `maxNSU` nem pelo NSU de uma rejeição.
3. Preservar a resposta antes de interpretar o XML. Gravar dados, cursor e confirmação de processamento na mesma transação.
4. Ao receber 137 ou alcançar `ultNSU == maxNSU`, encerrar a distribuição. O limite por execução automática é de 50 lotes, configurável entre 1 e 500.
5. Respeitar cooldown de 60 minutos, bloqueios persistidos, trava única e janela. Reiniciar não remove o bloqueio.
6. Em 656, registrar pausa e divergência válida como intervalo para conferência; manter o cursor de sucesso. O NSU informado pela rejeição é diagnóstico. Preservar o cursor é a política do AtlasDFE para evitar saltos sem processamento; não significa que a especificação proíba todo uso do cursor retornado em 656.

O agendador pode retomar ao expirar o cooldown dentro da janela. Assim, um disparo às 23:00 pode ser registrado como não executado e ter execução efetiva às 23:01. Falhas de comunicação/configuração e execuções parciais aguardam a próxima oportunidade programada, respeitando os controles. Ao reiniciar, somente a oportunidade mais recente elegível é recuperada; não se executa uma sequência de todos os horários perdidos.

## Recuperação por consNSU

Configuração atual: `NFE_RECOVERY_ENABLED=0`. A recuperação pontual permanece pausada e as pendências continuam no banco.

Quando habilitada e existem lacunas, a execução prioriza recuperação antes de nova distribuição. O app limita a até 20 consultas pontuais por execução e considera reservas de consultas por NSU/chave na última hora no mesmo banco. Esse contador não conhece o consumo do Qive ou de outra instalação. A rejeição 656 interrompe o trabalho e preserva a pausa.

O limite de 20 refere-se à recuperação pontual; não significa que o `distNSU` receba somente 20 documentos. Intervalos já completos não precisam ser reconsultados. Um documento recebido por `distNSU` também pode completar um intervalo registrado. Por isso, “Intervalo recuperado” não comprova uma execução recente de `consNSU`; consulte as contagens por origem nos logs.

Para testar recuperação: identificar pendência real, registrar cursores e contagens, fazer backup, habilitar `NFE_RECOVERY_ENABLED=1`, reiniciar e executar dentro da janela. Verificar documentos recebidos, indisponíveis e faltantes separadamente. Não criar lacunas ou alterar NSUs manualmente apenas para testar.

## Cancelamentos e papel do CNPJ

A distribuição depende do papel do CNPJ no documento. A tabela de distribuição da NT 2014.002 não disponibiliza ao emitente o evento de cancelamento da própria NF-e por esse serviço. Portanto, atingir `ultNSU == maxNSU` não comprova que todos os cancelamentos das notas emitidas pela Yorozu foram registrados localmente.

Para essas notas, importe o XML do evento de cancelamento ou registre a marcação administrativa com base em evidência externa. Importar novamente apenas o XML original da NF-e não acrescenta um evento ausente. O AtlasDFE não possui integração automática com a API do Synchro.

Referência: [NT 2014.002 — tabela de documentos distribuídos](https://www.nfe.fazenda.gov.br/Portal/exibirArquivo.aspx?AspxAutoDetectCookieSupport=1&conteudo=155Wx5%2FJB5A%3D).

## Evidência dos testes noturnos

| Última gravação apresentada | ultNSU = maxNSU | Resultado observado |
| --- | --- | --- |
| 15/10/2026 03:01 | `000000000568923` | Distribuição atualizada; consNSU pausado |
| 16/10/2026 03:00 | `000000000569252` | Distribuição atualizada; consNSU pausado |

Os trechos fornecidos mostram execuções concluídas, pausas respeitadas e avanço do cursor, sem rejeição 656 exibida nesses trechos. São evidência favorável da instalação testada; não comprovam ausência de conflito em todos os cenários nem a configuração interna do Qive. Igualdade de cursores indica atualização até o limite retornado naquela consulta, não integridade irrestrita de todo o histórico.

## Alternativa Arquivei Gateway

O suporte informou o domínio `https://sefaz-ws.arquivei.com.br/` e descreveu a troca do destino das chamadas mantendo a estrutura de consulta. O domínio isolado não define o endpoint completo nem a autenticação. Também informou compatibilidade com `consNSU`, eventos distribuídos e consultas simultâneas por captura centralizada; essas capacidades não foram validadas pelo AtlasDFE.

Antes de integrar, obter do fornecedor: contratação/habilitação, endpoint completo, autenticação, contrato SOAP, política de retenção, limites, semântica dos NSUs, eventos disponíveis e tratamento de falhas. Não assumir recuperação de qualquer NSU retroativo ou ausência absoluta de 656. O AtlasDFE continua usando diretamente o endpoint SEFAZ configurado no código; não há opção pronta de Gateway no Admin.

## Análise de incidentes

Em 656 ou divergência: preservar banco e logs, respeitar a próxima tentativa indicada e reunir horário/fuso, etapa, NSU inicial/enviado/informado/salvo, código/motivo, lotes gravados e referência da execução. Verificar se há outra instância do AtlasDFE e comparar o horário da falha com os registros de captura do Qive. Não apagar pendências, zerar cursor ou alternar bancos para contornar o bloqueio.
