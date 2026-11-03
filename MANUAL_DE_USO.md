# Operação do AtlasDFE

## Acesso

A aplicação abre diretamente para consultas e relatórios, sem login ou cadastro de usuários. A área Admin, importações e alterações manuais da situação de notas (marcar como cancelada ou remover a marcação) exigem login administrativo. Abra **Admin** e entre com a conta configurada em `CONFIGURAR_ADMIN.cmd`. Sem conta configurada, as ações administrativas permanecem bloqueadas.

Consultas e relatórios usam a identidade compartilhada `usuario-interno`; ações administrativas usam o nome da conta autenticada. **Sair do Admin** revoga a sessão e retorna ao acesso comum. A sessão expira após 30 minutos de inatividade (configurável), com duração máxima de oito horas. Contas comuns antigas são preservadas, mas não autorizam login no modo padrão.

As senhas do banco SQLCipher, certificado e SMTP continuam necessárias. Formulários mantêm a validação CSRF e as consultas à SEFAZ continuam limitadas à janela das 22h às 04h, com cooldown e trava.

Quando o TI habilitar o modo corporativo, a autenticação acontece no gateway da empresa. A interface exibe o nome fornecido e as funções administrativas dependem dos grupos atribuídos. Não há cadastro nem troca de senha corporativa no AtlasDFE; encerramento de sessão fica a cargo do gateway.

## Consulta de manifestações

Filtre por data do evento, número da nota, chave, manifestação e situação. A data final inclui o dia inteiro. As páginas mostram até 100 registros. Emitente sem nome disponível é identificado pelo CNPJ extraído da chave.

**Exportar Excel** inclui todas as páginas dos filtros aplicados, até 200 mil eventos. Chaves, protocolos e NSUs são texto. A consulta por TXT oferece **CHAVES.txt** (uma chave de 44 dígitos por linha) e **NOTAS.txt** (um número de NF de 1 a 9 dígitos por linha). Ambas usam exclusivamente o histórico local, com limite de 10 mil entradas e 2 MiB (2 × 1.024 × 1.024 bytes). O ícone de interrogação abre o tutorial.

Para números, salve um TXT UTF-8, sem cabeçalho, série ou pontuação:

```text
12345
67890
95022
```

Zeros à esquerda são aceitos e duplicatas numéricas são consideradas uma vez. A consulta inclui todas as chaves correspondentes ao número no banco do CNPJ configurado, sem escolher emitente ou série arbitrariamente. A planilha mantém chave/série e inclui **Numeros solicitados**, com contagem das correspondências e números não localizados. Não localizar significa ausência no banco disponível. Notas conhecidas sem manifestações também são incluídas. O relatório recusa resultados com mais de 100 mil notas; reduza a lista nesse caso.

Ausência de evento significa ausência no banco disponível. A tela não consulta a situação atual da NF-e na SEFAZ.

## Emissão e importação

Em **Emissão → Importar → Importar XML**, envie os XMLs completos ou o ZIP obtido no Synchro. Subpastas relativas de vários meses são percorridas automaticamente, inclusive pastas com horário no nome; não é necessário extrair ou reorganizar os XMLs. O app lê diretamente os membros do ZIP, sem extrair seus caminhos no disco. Caminhos absolutos/de unidade, referências à pasta superior, links simbólicos e dois-pontos no nome final do XML são recusados com motivo específico no registro. Confira importados, duplicados e recusados; o registro do lote é acessível na tela Importar, com permissão administrativa; no modo padrão, todos os acessos de rede têm essa permissão. A importação acumula dados entre meses. Chaves duplicadas preservam a primeira importação.

Aceita NF-e modelo 55 emitida pelo CNPJ configurado, com ou sem envelope `nfeProc`, e cancelamentos homologados compatíveis. Verifica estrutura mínima e correspondência de chave/emitente/número/série; não valida assinatura digital, schema fiscal completo ou autorização atual.

| Limite | Valor |
| --- | --- |
| Arquivo XML | 8 MiB |
| Upload | 100 MiB |
| Conteúdo expandido | 250 MiB |
| Quantidade de arquivos | 5.000 |
| Exportação por relatório | 100.000 linhas |

O período em **Emissão** usa a data de emissão da nota; **Consulta** usa a data do evento. O filtro **Item** pesquisa código, descrição e NCM. Em notas/retenções, seleciona documentos com o item; no relatório de itens, seleciona as linhas correspondentes.

O Excel reúne Notas, Itens e Retencoes, com os filtros e todas as páginas. O CSV exporta a visão selecionada. Os ícones **Itens** e **Manifestações** abrem detalhes da nota; **Voltar** preserva filtros/página e restaura rolagem quando o navegador permitir.

Novos XMLs originais não ficam armazenados permanentemente nem podem ser baixados pelo app. Dados extraídos continuam disponíveis para relatórios; obtenha o original no Synchro. Para extrair futuramente um campo não armazenado, será necessário reobter o XML. Cópias legadas existentes não são removidas automaticamente.

## Situação e CT-e

A tela mantém os rótulos **Autorizada** e **Cancelada**. A situação é calculada pelo banco local: cancelamento recebido ou marcação manual resulta em **Cancelada**; nos demais casos, **Autorizada**. Não é consulta online de autorização. Cancelamento homologado pode chegar antes da nota; reimportar a nota não o desfaz.

Para notas emitidas pela Yorozu, o cancelamento próprio não retorna ao emitente pelo `NFeDistribuicaoDFe`. É necessário importar o evento homologado ou registrar uma correção manual com evidência externa. Reimportar o XML original autorizado não informa um cancelamento posterior. O lote de exportação do Synchro usado nesta instalação não inclui esses eventos; não presuma que ele atualiza cancelamentos. A integração automática com o Synchro não está implementada. Referência: [Distribuição e cancelamentos](DISTRIBUICAO_DFE_QIVE.md#cancelamentos-e-papel-do-cnpj).

Em **Emissão → Importar → Alterar situação de notas**, informe até 100 números/chaves e o motivo. Números ambíguos exigem chave. Se houver entrada inválida, nenhuma nota do pedido é alterada. **Remover marcação manual** desfaz somente a correção interna, preservando cancelamentos recebidos por XML/NSU. A operação não transmite evento à SEFAZ.

Abra **CT-e vinculado** para ver os conhecimentos recebidos. Número, chave, transportadora e data aparecem somente quando disponíveis. Cada vínculo é exibido em uma linha, com rolagem horizontal quando necessário. **Vínculo identificado** não implica chave conhecida. **CT-e cancelado** indica todos os vínculos apresentados cancelados; não cancela a NF-e. **Não localizado** indica ausência no banco, não inexistência na SEFAZ. Não há download de XML completo de CT-e. Nos relatórios Excel, a coluna indica **CT-e Vinculado** quando há associação registrada ou **Não localizado** quando não há. Esse indicador não representa autorização atual; os detalhes e eventuais cancelamentos ficam na interface e no CSV.

Relatórios já gerados preservam o estado da geração. Gere novamente após importações ou correções.

## Downloads e cancelamento

Os relatórios ficam disponíveis por 24 horas após a geração, em `dados/downloads`, fora do banco fiscal. Repetir o download não renova a validade. A tabela informa documentos processados, período, geração, expiração, tamanho e estado. Nos relatórios, documentos representam notas distintas, não necessariamente linhas.

| Estado/ação | Comportamento |
| --- | --- |
| Na fila | Aguarda vaga de geração |
| Gerando arquivo | Trabalho em segundo plano; progresso sem percentual de conclusão |
| Cancelar solicitação | Remove da fila ou sinaliza interrupção da geração |
| Cancelando | Aguarda término e limpeza do trabalho ativo |
| Pronto | Arquivo disponível para transferência |
| Cancelar transferência | Interrompe o recebimento no navegador; preserva a cópia pronta |

No modo padrão, os acessos comuns compartilham a identidade técnica e podem cancelar as solicitações dessa identidade; administradores podem cancelar outras. No modo legado com contas, usuários cancelam suas próprias solicitações e administradores podem cancelar outras. O cancelamento é verificado durante SQL e montagem das linhas. A gravação final do Excel ainda pode concluir seu bloco antes de descartar o resultado. A porcentagem exibida na transferência mede bytes recebidos, não progresso de geração. Tarefas em andamento não sobrevivem ao reinício do servidor; arquivos concluídos permanecem até expirar.

## Status e sincronização

**Status** mostra disponibilidade, última gravação, `ultNSU`, `maxNSU` e próxima tentativa. **Abrir logs** exibe as sincronizações automáticas agrupadas por horário agendado, com resultado, documentos distNSU e manifestações adicionadas da última execução efetiva. Registros antigos usam faixas de data/hora quando comparáveis; sincronizações manuais permanecem separadas. Abra **Detalhes** para ver cada tentativa, motivo, contagens, diagnóstico, referência e próxima tentativa. Uma espera por intervalo fica junto da execução posterior; falhas permanecem nos detalhes. Contagens ausentes aparecem como —, sem assumir zero. `ultNSU == maxNSU` indica que a distribuição alcançou o limite informado naquele retorno; não garante que toda nota emitida já esteja disponível.

O AtlasDFE consulta externamente das **22:00 até antes de 04:00, Brasília**. Disparos padrão: **22h, 23h, 00h, 01h, 02h e 03h**. Manual, automático e retentativas compartilham banco, certificado, cursor, trava e cooldown. Em **Admin → Disparos automáticos**, marque ou desmarque **Ativar disparos automáticos**, informe os horários separados por vírgula e selecione os dias. Use **Salvar agendamento**. A mudança persiste e vale sem reiniciar. São aceitos até seis horários com intervalo mínimo de 60 minutos, dentro das 22h–04h. Os dias seguem a data civil: 01h de terça pertence à terça. Desativar impede novos disparos e retentativas; uma sincronização já iniciada termina normalmente. A reativação pode retomar a oportunidade mais recente da noite atual, respeitando bloqueios.

O processo precisa permanecer ativo. A rotina manual exige as confirmações exibidas pela interface.

`consNSU` permanece pausado (`NFE_RECOVERY_ENABLED=0`), com a fila preservada no banco. O bloco de intervalos antigos e o aviso repetido da fila não aparecem nos logs do Status. Recuperações efetivamente registradas continuam disponíveis nos detalhes da execução; pendências reais permanecem sinalizadas no resumo do Status.

Os registros de execução de sincronização são eliminados quando ultrapassam 30 dias. O log também gira por tamanho e pode descartar registros antes desse prazo; não há garantia de 30 dias completos. A limpeza não exclui notas, manifestações, NSUs ou pendências. Registros antigos podem usar o horário do servidor; registros identificados como Brasília devem ser lidos nesse fuso.

Regras detalhadas, atendimento Qive e tratamento de 656: [Distribuição DF-e e Qive](DISTRIBUICAO_DFE_QIVE.md).

O total de manifestações conta apenas os eventos de manifestação reconhecidos; cancelamentos não entram nessa contagem. Um intervalo completo indica presença dos documentos registrados, não atualização da situação fiscal de todas as notas.

## E-mail

Em **Admin → Alertas por e-mail**, configure remetente/usuário SMTP e até 20 destinatários. Os avisos automáticos são somente para novos eventos **Operação não Realizada (`210240`)** adicionados ao banco, mesmo quando a data do evento é antiga. Ciência da Operação não gera alerta. Reprocessar evento já registrado não cria outro aviso.

**Enviar e-mail de teste** envia mensagem sintética aos destinatários salvos, sem consultar a SEFAZ. Aguarde um minuto entre testes. Aceitação SMTP não confirma entrega; verifique entrada e spam.

**Tentar enviar pendentes** reprocessa a fila de e-mail, sem repetir sincronização. Falhas temporárias permanecem pendentes; destinatário removido encerra avisos correspondentes. Uma interrupção entre aceitação SMTP e confirmação local pode duplicar o envio.

Remetente e usuário salvos na interface valem nos próximos envios sem reiniciar. Servidor, porta e segurança são variáveis do processo; sua alteração exige reinício. Configuração técnica: [Servidor Web](SERVIDOR_WEB.md).

## Diagnóstico

| Ocorrência | Procedimento |
| --- | --- |
| Token inválido | Recarregar a página e repetir com o token atual; não reutilizar formulário aberto antes de uma troca de configuração |
| Intervalo ainda ativo | Aguardar a próxima tentativa indicada; não reiniciar para contornar |
| Rejeição 656 | Preservar cursor e logs, respeitar pausa e conferir janelas/instâncias |
| Resposta pendente | Verificar detalhe da falha; não apagar resposta ou avançar NSU |
| SMTP pendente | Conferir servidor, autenticação, destinatários e detalhe do erro |
| Relatório não reflete alteração | Gerar novo arquivo com os filtros necessários |
| Geração perdida após reinício | Solicitar novamente; tarefas são mantidas em memória |

## Guias do Admin

Cada tópico do Admin tem um botão **?** com instruções e limites da operação. Clique para abrir; use Escape ou clique fora para fechar. A janela SEFAZ e os estados de configuração permanecem visíveis sem abrir os guias.
