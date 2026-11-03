# Persistência, backup e restauração

## Arquivos operacionais

| Recurso | Localização padrão | Política |
| --- | --- | --- |
| Banco fiscal | Caminho em `secrets/db-path.txt`; fallback `dados/nfe_manifestacoes_seguro.db` | Histórico, NSUs, pendências, agendamento, dados extraídos e fila SMTP |
| Contas e sessões | `dados/admin_accounts.db` | Contas administrativas, hashes e sessões; contas comuns antigas preservadas |
| Configuração/segredos | `secrets/` | Caminhos, senhas e configuração de e-mail |
| Certificado A1 | Caminho configurado ou repositório Windows | Externo ao pacote; preservar chave privada |
| Auditoria | `logs/web_audit.log` e arquivos de rotação | Sincronizações: descarte após 30 dias e rotação por tamanho; demais registros por rotação |
| Relatórios temporários | `dados/downloads/` | Expiram após 24 horas |

Novos XMLs não são arquivados permanentemente. Dados extraídos, eventos, NSU/schema e classificação permanecem. Respostas SEFAZ aguardando processamento são mantidas até confirmação transacional. XMLs legados não são removidos automaticamente. A retenção de logs não reduz nem compacta o banco fiscal.

SQLCipher protege somente o banco fiscal configurado. Não cifra automaticamente banco de contas, planilhas, logs, certificados ou arquivos de segredos. Restrinja o acesso por permissões do sistema operacional e proteja o destino do backup.

## Backup consistente

O aplicativo **não possui backup completo automático agendado**. `CONFIGURAR_ADMIN.cmd` cria uma cópia do banco de contas antes de alterá-lo; isso não substitui o backup da instalação.

1. Pare o processo Web e confirme que nenhuma outra instância usa o banco.
2. Copie o banco fiscal do caminho efetivamente configurado, mesmo que externo à pasta do projeto. Preserve eventuais arquivos auxiliares `-wal`, `-shm` ou journal junto à cópia consistente; não os exclua manualmente.
3. Copie `dados/admin_accounts.db`, se existente, `secrets`, logs e certificados por arquivo. Para certificado instalado no Windows, preserve o PFX/P12 de origem e sua senha ou uma exportação com chave privada, se o certificado permitir exportação.
4. Preserve ambiente/variáveis do serviço e a versão/commit do código. Relatórios de `dados/downloads` são opcionais conforme a política interna; não substituem dados fiscais.
5. Registre data/hora, versão, commit, caminho do banco, `ultNSU`, `maxNSU` e responsável. Guarde em destino separado e com acesso restrito.

## Restauração

1. Mantenha o processo parado e preserve uma cópia do estado atual.
2. Restaure banco fiscal, banco de contas e configuração compatíveis. Reaplique permissões e acesso ao certificado.
3. Antes de iniciar, defina `NFE_AUTO_SYNC_ENABLED=0`. Se existir `secrets/auto-sync.json`, altere também `enabled` para `false` com o processo parado, preservando janela, horários e dias. A agenda salva prevalece sobre o ambiente; não exclua o arquivo. Verifique a instalação em ambiente isolado, sem chamadas reais à SEFAZ nem envio SMTP.
4. Confira acesso, banco, última gravação, cursores, intervalos pendentes e fila SMTP. Um backup anterior pode conter e-mails que já foram enviados depois de sua criação.
5. Compare os cursores com os registros operacionais atuais e com o histórico de captura. Restauração pode recuar o histórico local; não ajuste NSUs manualmente nem alterne bancos para contornar 656.
6. Retome a rotina automática somente após validar esse estado, dentro das regras de [Distribuição e Qive](DISTRIBUICAO_DFE_QIVE.md).

Teste periodicamente a restauração em cópia isolada, sem sincronização automática e sem envio SMTP real. Registre o resultado. O backup precisa incluir senha SQLCipher e meios de acesso ao certificado, guardados de forma protegida.

## Crescimento e retenção

O histórico fiscal não tem expiração automática. Monitore tamanho do banco, espaço livre e tempo de backup. A retenção de logs não elimina notas/eventos nem executa `VACUUM`; não há migração automática para PostgreSQL.

Os registros `sefaz_sync`, `sefaz_sync_auto` e `sefaz_scheduler` são elegíveis para exclusão após 30 dias. A rotação usa arquivos de até 5 MiB e cinco cópias, podendo descartar registros antes desse prazo. Diagnósticos e demais ações seguem a rotação por tamanho.

## Inspeção do banco

Use ferramenta compatível com SQLCipher quando o arquivo estiver cifrado, preferencialmente em modo somente leitura. Não edite tabelas de cursor, recuperação, respostas ou notificações para resolver erros operacionais. Migração/compactação e descarte de XMLs legados são operações separadas; atualizar o app não as executa automaticamente.
