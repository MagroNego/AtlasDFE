# Histórico de versões

## Revisão de 03/11/2026 — após 2.6.0

- Login apenas para Admin, importações e alteração manual da situação de notas; consultas e relatórios continuam livres.
- Janela SEFAZ configurável junto com a agenda, aplicada também às chamadas manuais e retentativas.
- Importação e visualização de CC-e; aba Resumo em Excel com hora de emissão e pesos, complementáveis por reimportação.
- Animações breves na interface, com redução de movimento respeitada.
- Manuais alinhados ao comportamento atual, com configuração administrativa, backup de contas e instruções operacionais diretas.

As entradas abaixo registram versões anteriores; permissões e horários antigos não descrevem a configuração atual.

## Emissão — 2026-10-23

- Botão Importar na Emissão reúne importação XML/ZIP e alteração manual da situação das notas, com resultado do lote e permissões administrativas preservados.

## Histórico de sincronização — 2026-10-21

- Agrupa tentativas automáticas por horário agendado, com compatibilidade por data/hora para registros antigos e detalhes preservados. Execuções manuais permanecem separadas.

- Execuções apresentadas em lista responsiva, com resultado e contagens visíveis e diagnóstico recolhido.
- Remove do Status o bloco de recuperações antigas e o aviso repetido de consNSU pausado, preservando auditoria, fila e avisos de pendências reais.
- Padroniza a apresentação das datas sem converter registros antigos de fuso desconhecido.

## Relatórios Excel — 2026-10-20

- Relatórios começam diretamente no cabeçalho da tabela, sem título, contagem ou linhas vazias antes dos dados.

- Formatação sóbria compartilhada: cabeçalhos grafite, fonte Calibri, linhas em cinza claro e dimensões ajustadas.
- Histórico com altura de linha proporcional ao conteúdo, filtros e painéis congelados preservados.
- Coluna de CT-e mostra CT-e Vinculado quando existe associação; interface e CSV mantêm os detalhes.

## Layout do Admin — 2026-10-20

- Orientações agrupadas em oito guias contextuais acessíveis pelo botão de interrogação.
- Menos texto permanente, janela SEFAZ visível e distribuição equilibrada dos painéis.
- Ajuste de navegação em telas pequenas e fechamento dos guias por Escape ou clique fora.

## Preparação de login corporativo — 2026-10-20

- Integração opcional com gateway autenticado, grupos de Consulta/Administrador e identificação individual na auditoria.
- Configuração incompleta bloqueia inicialização; credencial e grupos são validados no servidor. Login local permanece desativado.
- Contrato de cabeçalhos e roteiro de teste da integração em SERVIDOR_WEB.md.

## Main após a release 2.6.0 — revisão de 2026-10-19

A versão declarada continua 2.6.0. As alterações abaixo pertencem à main e não atualizam o ZIP já publicado dessa release.

- Acesso padrão sem login, cadastro ou senha administrativa; controle de acesso externo ao aplicativo. Contas antigas preservadas no modo legado opcional.
- Agenda configurável no Admin: ativação, dias e até seis horários, persistidos sem reinício, dentro da janela 22h–04h e separados por pelo menos 60 minutos.
- Sincronização manual bloqueada também no backend por janela, cooldown, CSRF e trava de execução.
- Consulta por NOTAS.TXT, correspondências locais e aba de conferência dos números solicitados.
- Importação de ZIP com subpastas relativas contendo dois-pontos em horários; mantém bloqueio de caminhos absolutos, travessia e links. Diagnóstico por arquivo no registro do lote.
- Cancelamento cooperativo de exportações durante SQL e montagem das linhas; otimização de planilhas e retorno imediato da solicitação de cancelamento.
- Detalhes de CT-e em uma linha, com rolagem horizontal.
- Cadastro por e-mail e melhorias de gestão de contas disponíveis somente no modo legado de autenticação.
- Documentação revisada contra o código: instalação, restauração com agenda desativada, retenção, implantação, Qive e limites de distribuição dos cancelamentos de emissão própria.

## 2.6.0 — 2026-10-16

- Versão estável com login obrigatório, cadastro próprio de usuário comum e acesso imediato.
- Gerenciamento administrativo de usuários, redefinição de senhas e exclusão com confirmação.
- Remetente SMTP editável e envio de mensagem de teste; alertas somente de Operação não Realizada.
- Retenção dos logs de sincronização por 30 dias, preservando auditoria administrativa e dados fiscais.
- Cancelamento de solicitações de relatório e interrupção da transferência na aba Downloads.
- Mantém rotina noturna horária, dados dos relatórios sem novos XMLs permanentes e consNSU pausado.
- Atualiza pacote e documentação com os horários e políticas atuais.

## 2.5.1 — 2026-10-14

- Sincronização automática a cada hora: 22h, 23h, 00h, 01h, 02h e 03h de Brasília.
- Mantém cooldown, pausas da SEFAZ, trava entre execuções e fechamento às 04h.

## 2.5.0 — 2026-10-14

- Identidade AtlasDFE com logos para fundo claro e escuro.
- Armazenamento permanente dos dados extraídos, sem novos XMLs originais; download de XML removido.
- Limpeza de respostas consNSU após processamento confirmado, preservando respostas pendentes e XMLs históricos.
- Consulta noturna das 22h às 04h, diagnóstico de erros e recuperação consNSU pausada para teste.

## 2.4.0 — 2026-10-05

- Login individual obrigatório para consultar qualquer dado fiscal, inclusive por links diretos.
- Perfis Consulta, Fiscal e Administrador; cadastro padrão com privilégio mínimo.
- Download de XML e exportações restritos a Fiscal/Administrador; importação, configuração e sincronização permanecem administrativas.
- Gestão de perfis e revogação de sessões ao alterar perfil, senha ou estado da conta.
- Migração preserva administradores e senhas existentes e encerra sessões anteriores.
- Proteção transacional do último administrador ativo; CSRF vinculado à sessão de todos os perfis.
- Auditoria de acessos com usuário e perfil; healthcheck sem dados sobre o banco.

## 2.3.0 — 2026-10-05

- Leitor XML de saída integrado com importação administrativa de XMLs ou ZIPs mensais.
- Arquivo cumulativo no banco fiscal, preservação do original e deduplicação pela chave.
- Consulta de notas, itens/impostos e retenções, filtros pela emissão e exportações Excel/CSV.
- Download do XML e acesso aos itens diretamente na consulta de manifestações.
- Histórico de importações e registro por arquivo; XMLs protegidos pelo SQLCipher quando o banco é criptografado.
- Limites de upload, ZIP e exportação, bloqueio de DTD/entidades e proteção contra fórmulas em planilhas.

## 2.2.2 — 2026-10-05

- Agendamento automático fixo às 08:00 e 15:00 de Brasília; removida a janela de meia-noite.
- Sincronização manual com confirmação explícita validada no servidor, confirmação final e prevenção de envio repetido.
- Cooldown do servidor fixado em 60 minutos, inclusive em instalações com configuração antiga.
- Máscara das datas durante a digitação e preenchimento do ano atual ao sair do campo ou consultar.

## 2.2.1 — 2026-10-05

- Cooldown padrão de 60 minutos, validado no servidor para atualização manual e automática.
- Reserva atômica no banco impede duas sincronizações simultâneas de ultrapassarem a checagem.
- Pausa 656 verificada antes do certificado, sem estender o bloqueio por tentativas recusadas.
- Testes de POST direto, agendamento bloqueado e disputa entre conexões.

## 2.2.0 — 2026-09-30

- Sincronização recorrente a cada oito horas, em janelas fixas de Brasília e com estado persistente por janela.
- Contas administrativas individuais e sessões revogadas no logout, troca de senha e alteração de estado da conta.
- Alertas por e-mail para novos eventos de Operação não Realizada, com fila persistente e administração de destinatários.
- Emitente na consulta e no Excel; histórico operacional no Status e histórico administrativo restrito.
- Limites de upload e exportação, controle de tentativas de login e dependências de segurança atualizadas.
- Retirada do filtro e da coluna de cancelamento da consulta e do Excel.
- Retirada do lançador de teste de data única; versão e documentação consolidadas.
- Pacote de instalação com lista explícita de arquivos e checksum SHA-256.

## 2.1.1

- Consulta com filtros e exportação; agendador interno e diagnóstico dos retornos 656.
- Retirada das interfaces CLI e desktop legadas.

## 2.0.1

- Segredos de runtime centralizados e ajustes de configuração e implantação.

## 2.0.0

- Interface Web com administração centralizada e consulta ao histórico local.
