import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from nfe_consulta.seguranca_banco import abrir_banco
from nfe_consulta.cte import SCHEMA as CTE_SCHEMA, save_events, linked_ctes
from nfe_consulta.cancelamento import cancelled_keys

from nfe_consulta.modelos import (
    Manifestacao,
    NfeLimiteConsultaErro,
    NfeErroResposta,
    ResultadoConsulta,
    RetornoDistribuicao,
)


NSU_INICIAL = "000000000000000"


class BancoManifestacoes:
    def __init__(self, caminho: str, senha: str | None = None):
        Path(caminho).parent.mkdir(parents=True, exist_ok=True)
        self.conexao = abrir_banco(caminho, senha)
        self.conexao.execute("PRAGMA foreign_keys = ON")
        self._criar_schema()

    def _criar_schema(self) -> None:
        self.conexao.executescript(CTE_SCHEMA)
        self.conexao.executescript(
            """
            CREATE TABLE IF NOT EXISTS documentos_nsu (
                cnpj TEXT NOT NULL, nsu TEXT NOT NULL, schema_xml TEXT NOT NULL,
                xml BLOB NOT NULL, classificacao TEXT NOT NULL,
                recebido_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(cnpj, nsu)
            );
            CREATE TABLE IF NOT EXISTS consultas_nsu (
                id INTEGER PRIMARY KEY, cnpj TEXT NOT NULL, nsu TEXT NOT NULL,
                tentado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                resposta_xml TEXT, resultado TEXT NOT NULL DEFAULT 'pendente'
            );
            CREATE INDEX IF NOT EXISTS idx_consultas_nsu ON consultas_nsu(cnpj, nsu);
            CREATE TABLE IF NOT EXISTS estado_distribuicao (
                cnpj TEXT PRIMARY KEY,
                ult_nsu TEXT NOT NULL,
                max_nsu TEXT NOT NULL,
                atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS respostas_distribuicao (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cnpj TEXT NOT NULL,
                nsu_enviado TEXT NOT NULL,
                resposta_xml TEXT,
                recebido_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                processado_em TEXT,
                resultado TEXT NOT NULL DEFAULT 'pendente',
                detalhe TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_respostas_distribuicao_pendentes
                ON respostas_distribuicao(cnpj, processado_em);
            CREATE TABLE IF NOT EXISTS recuperacoes_nsu (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cnpj TEXT NOT NULL,
                nsu_local TEXT NOT NULL,
                nsu_sefaz TEXT NOT NULL,
                registrado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                retomado_em TEXT,
                UNIQUE(cnpj, nsu_local, nsu_sefaz)
            );

            CREATE TABLE IF NOT EXISTS manifestacoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cnpj TEXT NOT NULL,
                chave TEXT NOT NULL,
                codigo TEXT NOT NULL,
                descricao TEXT NOT NULL,
                data_evento TEXT NOT NULL,
                protocolo TEXT NOT NULL,
                nsu TEXT NOT NULL,
                schema_xml TEXT NOT NULL,
                recebido_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(cnpj, chave, codigo, protocolo, nsu)
            );

            CREATE INDEX IF NOT EXISTS idx_manifestacoes_chave
                ON manifestacoes(chave);

            CREATE TABLE IF NOT EXISTS informacoes_nfe (
                cnpj TEXT NOT NULL,
                chave TEXT NOT NULL,
                emitente TEXT NOT NULL DEFAULT '',
                cancelada INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (cnpj, chave)
            );

            CREATE INDEX IF NOT EXISTS idx_manifestacoes_cnpj_data
                ON manifestacoes(cnpj, data_evento);

            CREATE TABLE IF NOT EXISTS consultas_pontuais (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cnpj TEXT NOT NULL,
                chave TEXT NOT NULL,
                consultado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_consultas_pontuais_hora
                ON consultas_pontuais(cnpj, consultado_em);

            CREATE TABLE IF NOT EXISTS pausa_distribuicao (
                cnpj TEXT PRIMARY KEY,
                ate_utc TEXT NOT NULL,
                motivo TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS controle_sincronizacao (
                cnpj TEXT PRIMARY KEY,
                ultima_tentativa_em TEXT NOT NULL,
                proxima_tentativa_em TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS controle_agendamento (
                cnpj TEXT PRIMARY KEY,
                ultima_execucao_local TEXT NOT NULL,
                atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS notificacoes_email (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manifestacao_id INTEGER NOT NULL REFERENCES manifestacoes(id),
                destinatario TEXT NOT NULL,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                enviado_em TEXT,
                cancelado_em TEXT,
                tentativas INTEGER NOT NULL DEFAULT 0,
                ultimo_erro TEXT,
                UNIQUE(manifestacao_id, destinatario)
            );
            """
        )
        colunas_email = {row[1] for row in self.conexao.execute("PRAGMA table_info(notificacoes_email)")}
        if "cancelado_em" not in colunas_email:
            self.conexao.execute("ALTER TABLE notificacoes_email ADD COLUMN cancelado_em TEXT")
        self.conexao.commit()

    def fechar(self) -> None:
        self.conexao.close()

    def obter_estado(self, cnpj: str) -> tuple[str, str]:
        linha = self.conexao.execute(
            "SELECT ult_nsu, max_nsu FROM estado_distribuicao WHERE cnpj = ?",
            (cnpj,),
        ).fetchone()
        return linha if linha else (NSU_INICIAL, NSU_INICIAL)

    def preservar_resposta(self, cnpj: str, enviado: str, xml: str) -> int:
        with self.conexao:
            cursor = self.conexao.execute(
                "INSERT INTO respostas_distribuicao(cnpj, nsu_enviado, resposta_xml) VALUES (?, ?, ?)",
                (cnpj, enviado, xml),
            )
        return cursor.lastrowid

    def respostas_pendentes(self, cnpj: str) -> list:
        return self.conexao.execute(
            "SELECT id, nsu_enviado, resposta_xml FROM respostas_distribuicao "
            "WHERE cnpj=? AND processado_em IS NULL ORDER BY id", (cnpj,),
        ).fetchall()

    def finalizar_resposta(self, identificador: int, resultado: str, detalhe: str = "") -> None:
        # O XML só é descartado após processamento confirmado. Mantém diagnóstico.
        self.conexao.execute(
            "UPDATE respostas_distribuicao SET processado_em=CURRENT_TIMESTAMP, "
            "resultado=?, detalhe=?, resposta_xml=NULL WHERE id=?",
            (resultado, detalhe[:1000], identificador),
        )

    def registrar_recuperacao_nsu(self, cnpj: str, enviado: str, informado: str | None) -> None:
        # Nunca recua o cursor, nem usa texto de xMotivo como fonte do NSU.
        local, _ = self.obter_estado(cnpj)
        if (informado is None or not informado.isascii() or not informado.isdigit()
                or len(informado) > 15 or int(informado) <= max(int(local), int(enviado))):
            return
        self.conexao.execute(
            "INSERT OR IGNORE INTO recuperacoes_nsu(cnpj, nsu_local, nsu_sefaz) VALUES (?, ?, ?)",
            (cnpj, local.zfill(15), informado.zfill(15)),
        )

    def nsu_para_consulta(self, cnpj: str) -> str:
        # O NSU retornado numa rejeição é diagnóstico, não confirmação de leitura.
        return self.obter_estado(cnpj)[0]

    def proximo_nsu_faltante(self, cnpj: str) -> str | None:
        linha = self.conexao.execute("""
            WITH candidatos(n) AS (
                SELECT CAST(nsu_local AS INTEGER)+1 FROM recuperacoes_nsu WHERE cnpj=?
                UNION SELECT CAST(d.nsu AS INTEGER)+1 FROM documentos_nsu d
                    JOIN recuperacoes_nsu g ON g.cnpj=d.cnpj AND d.nsu>=g.nsu_local AND d.nsu<g.nsu_sefaz WHERE d.cnpj=?
                UNION SELECT CAST(nsu AS INTEGER)+1 FROM consultas_nsu WHERE cnpj=?
            ) SELECT MIN(n) FROM candidatos
            WHERE EXISTS (SELECT 1 FROM recuperacoes_nsu g WHERE g.cnpj=?
                AND n>CAST(g.nsu_local AS INTEGER) AND n<=CAST(g.nsu_sefaz AS INTEGER))
            AND NOT EXISTS (SELECT 1 FROM documentos_nsu d WHERE d.cnpj=? AND d.nsu=printf('%015d', n))
            AND NOT EXISTS (SELECT 1 FROM consultas_nsu a WHERE a.cnpj=? AND a.nsu=printf('%015d', n) AND a.resultado IN ('pendente','recebido','indisponivel'))
            """, (cnpj,)*6).fetchone()
        return str(linha[0]).zfill(15) if linha[0] is not None else None

    def pausa_ativa(self, cnpj: str) -> str | None:
        linha = self.conexao.execute(
            "SELECT ate_utc, motivo FROM pausa_distribuicao WHERE cnpj = ?", (cnpj,)
        ).fetchone()
        if linha and datetime.fromisoformat(linha[0]) > datetime.now(timezone.utc):
            return f"{linha[1]} (nova tentativa apos {linha[0]})"
        return None

    def pausar_distribuicao(self, cnpj: str, motivo: str) -> None:
        ate = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="seconds")
        with self.conexao:
            self.conexao.execute(
                "INSERT INTO pausa_distribuicao(cnpj, ate_utc, motivo) VALUES (?, ?, ?) "
                "ON CONFLICT(cnpj) DO UPDATE SET ate_utc=excluded.ate_utc, motivo=excluded.motivo",
                (cnpj, ate, motivo),
            )

    def obter_janela_sincronizacao(
        self,
        cnpj: str,
    ) -> tuple[datetime | None, datetime | None]:
        linha = self.conexao.execute(
            "SELECT ultima_tentativa_em, proxima_tentativa_em "
            "FROM controle_sincronizacao WHERE cnpj = ?",
            (cnpj,),
        ).fetchone()
        if not linha:
            return None, None
        return datetime.fromisoformat(linha[0]), datetime.fromisoformat(linha[1])

    def bloqueio_sincronizacao(
        self,
        cnpj: str,
        agora: datetime | None = None,
    ) -> str | None:
        _, proxima = self.obter_janela_sincronizacao(cnpj)
        agora = agora or datetime.now(timezone.utc)
        if proxima and proxima > agora:
            return (
                "Sincronização temporariamente bloqueada. "
                f"Nova tentativa permitida após {proxima.isoformat(timespec='minutes')}."
            )
        return None

    def registrar_tentativa_sincronizacao(
        self,
        cnpj: str,
        cooldown_minutos: int,
        agora: datetime | None = None,
    ) -> datetime:
        if cooldown_minutos < 1:
            raise ValueError("cooldown_minutos deve ser maior que zero.")
        agora = agora or datetime.now(timezone.utc)
        proxima = agora + timedelta(minutes=cooldown_minutos)
        with self.conexao:
            self.conexao.execute(
                "INSERT INTO controle_sincronizacao("
                "cnpj, ultima_tentativa_em, proxima_tentativa_em"
                ") VALUES (?, ?, ?) "
                "ON CONFLICT(cnpj) DO UPDATE SET "
                "ultima_tentativa_em=excluded.ultima_tentativa_em, "
                "proxima_tentativa_em=excluded.proxima_tentativa_em",
                (
                    cnpj,
                    agora.isoformat(timespec="seconds"),
                    proxima.isoformat(timespec="seconds"),
                ),
            )
        return proxima

    def reservar_sincronizacao(self, cnpj: str, cooldown_minutos: int) -> datetime:
        """Verifica e reserva a janela numa transação, inclusive entre processos."""
        if cooldown_minutos < 60:
            raise ValueError("O cooldown deve ser de pelo menos 60 minutos.")
        self.conexao.execute("BEGIN IMMEDIATE")
        try:
            bloqueio = self.bloqueio_sincronizacao(cnpj) or self.pausa_ativa(cnpj)
            if bloqueio:
                raise NfeLimiteConsultaErro(bloqueio)
            return self.registrar_tentativa_sincronizacao(cnpj, cooldown_minutos)
        except Exception:
            self.conexao.rollback()
            raise

    def prorrogar_cooldown(self, cnpj: str, minutos: int) -> None:
        """Conta a próxima janela após o término, sem reduzir prazo já salvo."""
        proxima = (datetime.now(timezone.utc) + timedelta(minutes=minutos)).isoformat(timespec="seconds")
        with self.conexao:
            self.conexao.execute(
                "UPDATE controle_sincronizacao SET proxima_tentativa_em=? "
                "WHERE cnpj=? AND proxima_tentativa_em<?", (proxima, cnpj, proxima))

    def sincronizacao_recente_e_completa(self, cnpj: str) -> bool:
        linha = self.conexao.execute(
            "SELECT ult_nsu, max_nsu, atualizado_em FROM estado_distribuicao "
            "WHERE cnpj = ?", (cnpj,)
        ).fetchone()
        if not linha or linha[0] != linha[1]:
            return False
        atualizado = datetime.strptime(linha[2], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - atualizado < timedelta(hours=1)

    def salvar_manifestacoes(self, cnpj: str, retorno: RetornoDistribuicao) -> int:
        inseridos = 0
        with self.conexao:
            # Conserva o recebimento do NSU, sem uma cópia permanente do XML.
            for nsu, schema, xml, classificacao in retorno.documentos:
                self.conexao.execute(
                    "INSERT OR IGNORE INTO documentos_nsu(cnpj,nsu,schema_xml,xml,classificacao) VALUES (?,?,?,?,?)",
                    (cnpj, nsu, schema, b"", classificacao))
            save_events(self.conexao, cnpj, retorno.eventos_cte)
            for info in retorno.informacoes_notas:
                self.conexao.execute(
                    """INSERT INTO informacoes_nfe(cnpj, chave, emitente, cancelada)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(cnpj, chave) DO UPDATE SET
                        emitente = CASE WHEN excluded.emitente != '' THEN excluded.emitente ELSE informacoes_nfe.emitente END,
                        cancelada = MAX(informacoes_nfe.cancelada, excluded.cancelada)""",
                    (cnpj, info.chave, info.emitente, int(info.cancelada)),
                )
            for chave, evento in retorno.manifestacoes:
                cursor = self.conexao.execute(
                    """
                    INSERT OR IGNORE INTO manifestacoes
                    (cnpj, chave, codigo, descricao, data_evento, protocolo, nsu, schema_xml)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cnpj,
                        chave,
                        evento.codigo,
                        evento.descricao,
                        evento.data,
                        evento.protocolo,
                        evento.nsu,
                        evento.schema,
                    ),
                )
                inseridos += cursor.rowcount
        return inseridos

    def salvar_retorno(
        self, cnpj: str, retorno: RetornoDistribuicao,
        destinatarios_alerta: tuple[str, ...] = (),
        *, resposta_id: int | None = None, atualizar_estado: bool = True, consulta_nsu_id: int | None = None,
    ) -> int:
        if retorno.status_codigo not in (137, 138):
            raise NfeErroResposta("Resposta rejeitada não pode atualizar documentos ou cursor")
        anterior, _ = self.obter_estado(cnpj)
        if (not retorno.ult_nsu.isdigit() or not retorno.max_nsu.isdigit()
                or int(retorno.ult_nsu) < int(anterior)
                or int(retorno.ult_nsu) > int(retorno.max_nsu)):
            raise NfeErroResposta("Cursor NSU invalido ou anterior ao ja salvo; lote nao gravado")
        inseridos = 0
        with self.conexao:
            # A resposta bruta pendente só é limpa após esta transação confirmar.
            for nsu, schema, xml, classificacao in retorno.documentos:
                self.conexao.execute(
                    "INSERT OR IGNORE INTO documentos_nsu(cnpj,nsu,schema_xml,xml,classificacao) VALUES (?,?,?,?,?)",
                    (cnpj, nsu, schema, b"", classificacao))
            save_events(self.conexao, cnpj, retorno.eventos_cte)
            for info in retorno.informacoes_notas:
                self.conexao.execute(
                    """INSERT INTO informacoes_nfe(cnpj, chave, emitente, cancelada)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(cnpj, chave) DO UPDATE SET
                        emitente = CASE WHEN excluded.emitente != '' THEN excluded.emitente ELSE informacoes_nfe.emitente END,
                        cancelada = MAX(informacoes_nfe.cancelada, excluded.cancelada)""",
                    (cnpj, info.chave, info.emitente, int(info.cancelada)),
                )
            for chave, evento in retorno.manifestacoes:
                cursor = self.conexao.execute(
                    """INSERT OR IGNORE INTO manifestacoes
                    (cnpj, chave, codigo, descricao, data_evento, protocolo, nsu, schema_xml)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (cnpj, chave, evento.codigo, evento.descricao, evento.data,
                     evento.protocolo, evento.nsu, evento.schema),
                )
                inseridos += cursor.rowcount
                if cursor.rowcount and evento.codigo == "210240":
                    for destinatario in destinatarios_alerta:
                        self.conexao.execute(
                            "INSERT OR IGNORE INTO notificacoes_email(manifestacao_id, destinatario) "
                            "VALUES (?, ?)",
                            (cursor.lastrowid, destinatario),
                        )
            if atualizar_estado:
                self.conexao.execute(
                    """
                    INSERT INTO estado_distribuicao(cnpj, ult_nsu, max_nsu)
                    VALUES (?, ?, ?)
                    ON CONFLICT(cnpj) DO UPDATE SET
                        ult_nsu = excluded.ult_nsu,
                        max_nsu = excluded.max_nsu,
                        atualizado_em = CURRENT_TIMESTAMP
                    """,
                    (cnpj, retorno.ult_nsu, retorno.max_nsu),
                )
            self.conexao.execute(
                "UPDATE recuperacoes_nsu SET retomado_em=CURRENT_TIMESTAMP "
                "WHERE cnpj=? AND retomado_em IS NULL AND CAST(nsu_sefaz AS INTEGER)<=?",
                (cnpj, int(retorno.ult_nsu)),
            )
            if consulta_nsu_id is not None:
                self.conexao.execute("UPDATE consultas_nsu SET resultado='recebido',resposta_xml=NULL WHERE id=?", (consulta_nsu_id,))
            if resposta_id is not None:
                self.finalizar_resposta(resposta_id, str(retorno.status_codigo),
                    f"ultNSU={retorno.ult_nsu}; maxNSU={retorno.max_nsu}; documentos={len(retorno.documentos)}")
        return inseridos

    def notificacoes_pendentes(self, limite: int = 100, *, incluir_codigo: bool = False) -> list[tuple]:
        return self.conexao.execute(
            ("SELECT n.id, n.destinatario, m.chave, m.data_evento, m.protocolo "
             + (", m.codigo " if incluir_codigo else "")) +
            "FROM notificacoes_email n JOIN manifestacoes m ON m.id = n.manifestacao_id "
            "WHERE n.enviado_em IS NULL AND n.cancelado_em IS NULL ORDER BY n.id LIMIT ?",
            (limite,),
        ).fetchall()

    def contar_notificacoes_pendentes(self) -> int:
        return self.conexao.execute(
            "SELECT COUNT(*) FROM notificacoes_email WHERE enviado_em IS NULL AND cancelado_em IS NULL"
        ).fetchone()[0]

    def registrar_envio_email(self, id_notificacao: int, erro: str | None = None) -> None:
        with self.conexao:
            self.conexao.execute(
                "UPDATE notificacoes_email SET tentativas = tentativas + 1, "
                "enviado_em = CASE WHEN ? IS NULL THEN CURRENT_TIMESTAMP ELSE enviado_em END, "
                "ultimo_erro = ? WHERE id = ? AND enviado_em IS NULL AND cancelado_em IS NULL",
                (erro, erro, id_notificacao),
            )

    def cancelar_notificacao(self, id_notificacao: int, motivo: str) -> None:
        with self.conexao:
            self.conexao.execute(
                "UPDATE notificacoes_email SET cancelado_em=CURRENT_TIMESTAMP, ultimo_erro=? "
                "WHERE id=? AND enviado_em IS NULL AND cancelado_em IS NULL",
                (motivo, id_notificacao),
            )

    def cancelar_destinatarios_removidos(self, destinatarios: tuple[str, ...]) -> int:
        with self.conexao:
            placeholders = ",".join("?" for _ in destinatarios)
            condicao = f" AND destinatario NOT IN ({placeholders})" if destinatarios else ""
            cursor = self.conexao.execute(
                "UPDATE notificacoes_email SET cancelado_em=CURRENT_TIMESTAMP, ultimo_erro='destinatario_removido' "
                "WHERE enviado_em IS NULL AND cancelado_em IS NULL" + condicao,
                destinatarios,
            )
        return cursor.rowcount

    def consultas_na_ultima_hora(self, cnpj: str) -> int:
        linha = self.conexao.execute(
            """
            SELECT COUNT(*) FROM consultas_pontuais
            WHERE cnpj = ? AND consultado_em >= datetime('now', '-1 hour')
            """,
            (cnpj,),
        ).fetchone()
        return int(linha[0])

    def chave_consultada_na_ultima_hora(self, cnpj: str, chave: str) -> bool:
        linha = self.conexao.execute(
            """
            SELECT 1 FROM consultas_pontuais
            WHERE cnpj = ? AND chave = ?
              AND consultado_em >= datetime('now', '-1 hour')
            LIMIT 1
            """,
            (cnpj, chave),
        ).fetchone()
        return linha is not None

    def validar_limite_pontual(self, cnpj: str, chaves: list[str]) -> None:
        novas = {
            chave
            for chave in chaves
            if not self.chave_consultada_na_ultima_hora(cnpj, chave)
        }
        usadas = self.consultas_na_ultima_hora(cnpj) + self.conexao.execute(
            "SELECT COUNT(*) FROM consultas_nsu WHERE cnpj=? AND tentado_em>=datetime('now','-1 hour')", (cnpj,)).fetchone()[0]
        if usadas + len(novas) > 20:
            disponiveis = max(0, 20 - usadas)
            raise NfeLimiteConsultaErro(
                f"Limite preventivo: apenas {disponiveis} consulta(s) disponivel(is) "
                "nesta janela de uma hora. Nenhuma chamada foi enviada."
            )

    def registrar_consulta_pontual(self, cnpj: str, chave: str) -> None:
        with self.conexao:
            self.conexao.execute(
                "INSERT INTO consultas_pontuais(cnpj, chave) VALUES (?, ?)",
                (cnpj, chave),
            )

    def consultar_chave(self, chave: str, cnpj: str | None = None) -> ResultadoConsulta:
        return consultar_chave_local(self.conexao, chave, cnpj)


def consultar_chave_local(conexao, chave: str, cnpj: str | None = None) -> ResultadoConsulta:
    sql = (
        "SELECT codigo, descricao, data_evento, protocolo, nsu, schema_xml "
        "FROM manifestacoes WHERE chave = ?"
    )
    parametros = [chave]
    if cnpj:
        sql += " AND cnpj = ?"
        parametros.append(cnpj)
    sql += " ORDER BY data_evento, CAST(nsu AS INTEGER), id"
    linhas = conexao.execute(sql, parametros).fetchall()
    eventos = tuple(Manifestacao(*linha) for linha in linhas)
    return ResultadoConsulta(
        chave=chave,
        status_codigo=0,
        status_motivo="Historico local da Distribuicao DF-e",
        protocolo_nfe=None,
        manifestacoes=eventos,
        ctes=linked_ctes(conexao, cnpj, [chave]).get(chave, ()) if cnpj else (),
        cancelada=chave in cancelled_keys(conexao, cnpj, [chave]),
    )
