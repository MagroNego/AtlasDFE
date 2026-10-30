# Proposta: janela SEFAZ configurável no Admin

Base: main `4890e05ec79807112f22f487a77475283127f5e6`. Estado: proposta para aprovação, sem implementação.

## Objetivo

Permitir ao administrador configurar início e fim da janela de consultas externas em horário de Brasília, preservando o padrão atual 22:00–04:00 e os controles de pausa/cooldown. A main permanece intacta; a futura implementação será feita em outra branch.

## Alternativas

1. Alterar somente os disparos. Já é possível hoje, mas não muda a janela permitida e não atende ao novo pedido.
2. Configurar a janela e usar a mesma regra nos disparos, consultas manuais, recuperação e interface. Recomendação por atender ao pedido e evitar inconsistências.

A segunda alternativa é proposta técnica, ainda não aprovada pelo usuário.

## Comportamento proposto

- Dois campos de horário no formulário existente do Admin: início e fim, identificados como horário de Brasília.
- Início inclusivo e fim exclusivo. Suportar intervalo no mesmo dia ou atravessando a meia-noite. Início igual ao fim é inválido, para evitar liberar 24 horas acidentalmente.
- Manter os campos existentes de horários dos disparos e dias da semana. Um único salvamento deve validar a janela e os disparos juntos; horários fora da janela são rejeitados com mensagem clara, sem corrigir silenciosamente.
- Preservar o limite atual de até seis disparos e o intervalo mínimo de 60 minutos, incluindo proximidade entre o último horário do dia e o primeiro do seguinte.
- Manter os dias como datas civis de Brasília, conforme a agenda atual.
- Instalação sem novos campos continua usando 22:00–04:00. Arquivo legado continua compatível.
- Salvar em auto-sync.json com substituição atômica; erro de validação/escrita mantém configuração anterior. A configuração é restrita a admin e protegida por CSRF.
- Registrar início/fim anteriores e novos na auditoria, sem senhas, XMLs ou certificados.
- Mudança válida afeta os próximos disparos e tentativas sem reiniciar o processo. Salvar configuração não faz consulta à SEFAZ e não modifica ultNSU/maxNSU.
- Uma chamada já enviada pode terminar; antes da próxima chamada/lote, verificar a janela vigente. Encerrar a janela não interrompe uma gravação local transacional.
- Manter os bloqueios de consumo indevido, o cooldown mínimo de 60 minutos e a configuração atual de recuperação consNSU.
- A janela é um acordo operacional com o Qive, não um horário imposto pela SEFAZ. O formulário deve lembrar brevemente a necessidade de evitar sobreposição com outro coletor.
- Consultas ao banco e exportações locais continuam disponíveis fora da janela.

## Superfícies a atualizar

Centralizar cálculo/validação da janela em `janela_consulta.py`; integrar leitura persistente e validação da agenda em `web/auto_sync_config.py` e `web/settings.py`. Atualizar chamadas em sync_runtime, scheduler, serviço, distribuição e recuperação para usar a mesma política, com isolamento entre instâncias/testes e sem estado global mutável desprotegido. Atualizar status_view, templates e mensagens de diagnóstico para refletir os valores atuais. Não mexer na operação de manifestação fiscal, que tem fluxo separado.

## Verificação necessária

1. Bordas 21:59/22:00/03:59/04:00 no padrão; janelas diurnas, noturnas e com minutos; rejeição de formato e intervalo vazio.
2. Próxima abertura em Brasília e conversão de instantes UTC; cooldown/pause que terminam depois do fechamento; fins de semana/dias selecionados.
3. Compatibilidade do JSON antigo, gravação atômica e preservação após erro; persistência após reinício.
4. Autorização e CSRF; usuário comum não pode salvar.
5. Reconfiguração do scheduler sem reinício, sem disparo fora da janela nem consulta durante o salvamento; nova trava entre lotes e preservação do cursor.
6. Status e Admin apresentam a mesma janela e próxima execução; conferir visualmente com o layout existente.
7. Executar testes existentes e novos em sandbox com banco fictício, sem certificado real e sem rede externa. No ambiente desta revisão, essa execução está bloqueada pela indisponibilidade de namespace de rede no bubblewrap.

## Escopo separado

As melhorias de segurança/desempenho do relatório não fazem parte automaticamente deste campo. Devem ser aplicadas e verificadas em mudanças pequenas, para facilitar comparação e evitar alterar permissões, layout ou resultado fiscal junto com o agendamento.
