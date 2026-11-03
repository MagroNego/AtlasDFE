"""Consultas somente leitura no banco local de manifestações."""

from __future__ import annotations

from nfe_consulta.export_control import cancellable_rows
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from math import ceil
from pathlib import Path

from nfe_consulta.seguranca_banco import abrir_banco
from nfe_consulta.cte import linked_ctes
from nfe_consulta.cancelamento import cancellation_condition, cancelled_keys, status_label, lookup_cancelled


TIPOS_MANIFESTACAO = {
    "110110": "Carta de Correção",
    "210200": "Confirmação da Operação",
    "210210": "Ciência da Operação",
    "210220": "Desconhecimento da Operação",
    "210240": "Operação não Realizada",
}


@dataclass(frozen=True)
class FiltrosEventos:
    data_inicial: date | None = None
    data_final: date | None = None
    numero: int | None = None
    serie: int | None = None
    chave: str | None = None
    codigo: str | None = None
    situacao: str = ""

    @property
    def data_inicial_texto(self) -> str:
        return self.data_inicial.isoformat() if self.data_inicial else ""

    @property
    def data_final_texto(self) -> str:
        return self.data_final.isoformat() if self.data_final else ""

    @property
    def data_inicial_br(self) -> str:
        return self.data_inicial.strftime("%d/%m/%Y") if self.data_inicial else ""

    @property
    def data_final_br(self) -> str:
        return self.data_final.strftime("%d/%m/%Y") if self.data_final else ""

    @property
    def numero_texto(self) -> str:
        return str(self.numero) if self.numero is not None else ""

    @property
    def serie_texto(self) -> str:
        return str(self.serie) if self.serie is not None else ""


@dataclass(frozen=True)
class EventoNota:
    numero: int
    serie: str
    chave: str
    codigo: str
    descricao: str
    data: str
    protocolo: str
    nsu: str
    recebido_em: str = ""
    emitente: str = ""
    ctes: tuple = ()
    cancelada: bool = False
    correcao: str = ""
    sequencia: str = ""

    @property
    def situacao(self):
        return status_label(self.cancelada)

    @property
    def data_label(self) -> str:
        return _data_label(self.data)

    @property
    def recebido_label(self) -> str:
        return _data_label(self.recebido_em, recebido_utc=True)


@dataclass(frozen=True)
class ResultadoEventos:
    eventos: tuple[EventoNota, ...]
    total: int
    pagina: int
    por_pagina: int
    notas_canceladas: tuple = ()

    @property
    def paginas(self) -> int:
        return ceil(self.total / self.por_pagina) if self.total else 0

    @property
    def primeiro(self) -> int:
        return ((self.pagina - 1) * self.por_pagina + 1) if self.total else 0

    @property
    def ultimo(self) -> int:
        return min(self.pagina * self.por_pagina, self.total) if self.total else 0


def _data_label(valor: str, *, recebido_utc: bool = False) -> str:
    valor = (valor or "").strip()
    if not valor:
        return "—"
    try:
        instante = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return valor
    sufixo = " UTC" if recebido_utc and instante.tzinfo is None else ""
    return instante.strftime("%d/%m/%Y %H:%M:%S") + sufixo


def _parse_data(valor: str, rotulo: str) -> date | None:
    valor = valor.strip()
    if not valor:
        return None

    if len(valor) == 5 and valor[2] == "/":
        ano = datetime.now(timezone(timedelta(hours=-3))).year
        valor = f"{valor}/{ano}"
    elif len(valor) == 4 and valor.isdigit():
        ano = datetime.now(timezone(timedelta(hours=-3))).year
        valor = f"{valor[:2]}/{valor[2:]}/{ano}"

    formatos = ("%Y-%m-%d", "%d/%m/%Y", "%d%m%Y")
    for formato in formatos:
        try:
            return datetime.strptime(valor, formato).date()
        except ValueError:
            pass

    raise ValueError(f"{rotulo} inválida. Use DD/MM/AAAA.")


def normalizar_filtros(
    *,
    data_inicial: str = "",
    data_final: str = "",
    numero: str = "",
    serie: str = "",
    chave: str = "",
    codigo: str = "",
    situacao: str = "",
) -> FiltrosEventos:
    inicio = _parse_data(data_inicial, "Data inicial")
    fim = _parse_data(data_final, "Data final")
    if inicio and fim and inicio > fim:
        raise ValueError("A data inicial não pode ser posterior à data final.")

    numero_limpo = numero.strip()
    if numero_limpo and not numero_limpo.isdigit():
        raise ValueError("Informe somente números no campo Número da NF.")
    numero_valor = int(numero_limpo) if numero_limpo else None
    if numero_valor is not None and not 1 <= numero_valor <= 999_999_999:
        raise ValueError("Número da NF deve estar entre 1 e 999999999.")

    serie_limpa = serie.strip()
    if serie_limpa and not serie_limpa.isdigit():
        raise ValueError("Informe somente números no campo Série.")
    serie_valor = int(serie_limpa) if serie_limpa else None
    if serie_valor is not None and not 0 <= serie_valor <= 999:
        raise ValueError("Série deve estar entre 0 e 999.")

    chave_limpa = "".join(chave.split())
    if chave_limpa:
        if not chave_limpa.isdigit() or len(chave_limpa) != 44:
            raise ValueError("A chave da NF-e deve conter exatamente 44 dígitos.")
    else:
        chave_limpa = None

    codigo_limpo = codigo.strip()
    if codigo_limpo and codigo_limpo not in TIPOS_MANIFESTACAO:
        raise ValueError("Tipo de manifestação inválido.")
    if situacao not in ('', 'autorizada', 'cancelada'):
        raise ValueError('Situação inválida.')

    return FiltrosEventos(
        data_inicial=inicio,
        data_final=fim,
        numero=numero_valor,
        serie=serie_valor,
        chave=chave_limpa,
        codigo=codigo_limpo or None,
        situacao=situacao,
    )


def _where_eventos(
    cnpj: str,
    filtros: FiltrosEventos,
    tem_informacoes: bool = True,
    cancellation_sql: str | None = None,
) -> tuple[str, list[object]]:
    clausulas = ["cnpj = ?"]
    parametros: list[object] = [cnpj]
    if filtros.situacao:
        cancelled = cancellation_sql or ('EXISTS (SELECT 1 FROM informacoes_nfe i WHERE i.cnpj=manifestacoes.cnpj AND i.chave=manifestacoes.chave AND i.cancelada=1)' if tem_informacoes else '0=1')
        clausulas.append(cancelled if filtros.situacao == 'cancelada' else f'NOT ({cancelled})')

    if filtros.data_inicial:
        clausulas.append("data_evento >= ?")
        parametros.append(filtros.data_inicial.isoformat())

    if filtros.data_final:
        dia_seguinte = filtros.data_final + timedelta(days=1)
        clausulas.append("data_evento < ?")
        parametros.append(dia_seguinte.isoformat())

    if filtros.numero is not None:
        clausulas.append("CAST(SUBSTR(chave, 26, 9) AS INTEGER) = ?")
        parametros.append(filtros.numero)

    if filtros.serie is not None:
        clausulas.append("CAST(SUBSTR(chave, 23, 3) AS INTEGER) = ?")
        parametros.append(filtros.serie)

    if filtros.chave:
        clausulas.append("chave = ?")
        parametros.append(filtros.chave)

    if filtros.codigo:
        clausulas.append("codigo = ?")
        parametros.append(filtros.codigo)

    return " AND ".join(clausulas), parametros


def _evento_da_linha(linha) -> EventoNota:
    chave, codigo, descricao, data_evento, protocolo, nsu, recebido_em, emitente, correcao, sequencia = linha
    numero = int(chave[25:34])
    serie = chave[22:25].lstrip("0") or "0"
    return EventoNota(
        correcao=correcao,
        sequencia=sequencia,
        numero=numero,
        serie=serie,
        chave=chave,
        codigo=codigo,
        descricao=TIPOS_MANIFESTACAO.get(codigo, descricao),
        data=data_evento,
        protocolo=protocolo,
        nsu=nsu,
        recebido_em=recebido_em,
        emitente=emitente or (chave[6:20] if len(chave) == 44 and chave.isdigit() else ""),
    )


def _tem_informacoes_nfe(conexao) -> bool:
    """Bancos existentes continuam consultáveis antes da próxima sincronização."""
    return conexao.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='informacoes_nfe'"
    ).fetchone() is not None


def _campos_nota(tem_informacoes: bool) -> str:
    if not tem_informacoes:
        return "'' AS emitente"
    return """COALESCE((SELECT emitente FROM informacoes_nfe
                        WHERE cnpj=manifestacoes.cnpj AND chave=manifestacoes.chave), '') AS emitente"""



def _campos_correcao(conexao):
    if not conexao.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cartas_correcao'").fetchone():
        return "'' AS correcao, '' AS sequencia"
    return """COALESCE((SELECT correcao FROM cartas_correcao c WHERE
        manifestacoes.codigo='110110' AND c.cnpj=manifestacoes.cnpj AND c.chave=manifestacoes.chave
        AND c.protocolo=manifestacoes.protocolo), '') AS correcao,
        COALESCE((SELECT sequencia FROM cartas_correcao c WHERE
        manifestacoes.codigo='110110' AND c.cnpj=manifestacoes.cnpj AND c.chave=manifestacoes.chave
        AND c.protocolo=manifestacoes.protocolo), '') AS sequencia"""


def consultar_eventos(
    database_path: Path,
    cnpj: str,
    filtros: FiltrosEventos,
    *,
    password: str | None = None,
    pagina: int = 1,
    por_pagina: int = 100,
) -> ResultadoEventos:
    """Consulta eventos com filtros e paginação sem acessar a SEFAZ."""
    if pagina < 1:
        raise ValueError("Página inválida.")
    if not 1 <= por_pagina <= 500:
        raise ValueError("Quantidade por página inválida.")

    conexao = abrir_banco(database_path, password, somente_leitura=True)
    try:
        tem_informacoes = _tem_informacoes_nfe(conexao)
        where, parametros = _where_eventos(cnpj, filtros, tem_informacoes, cancellation_condition(conexao, 'manifestacoes'))
        campos_nota = _campos_nota(tem_informacoes) + ", " + _campos_correcao(conexao)
        total = int(
            conexao.execute(
                f"SELECT COUNT(*) FROM manifestacoes WHERE {where}",
                parametros,
            ).fetchone()[0]
        )

        paginas = ceil(total / por_pagina) if total else 0
        pagina_efetiva = min(pagina, paginas) if paginas else 1
        offset = (pagina_efetiva - 1) * por_pagina

        linhas = conexao.execute(
            f"""
            SELECT
                chave,
                codigo,
                descricao,
                data_evento,
                protocolo,
                nsu,
                recebido_em,
                {campos_nota}
            FROM manifestacoes
            WHERE {where}
            ORDER BY data_evento DESC, CAST(nsu AS INTEGER) DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            [*parametros, por_pagina, offset],
        ).fetchall()
        ctes = linked_ctes(conexao, cnpj, [line[0] for line in linhas])
        cancelled = cancelled_keys(conexao, cnpj, [line[0] for line in linhas])
        cancelled_notes = lookup_cancelled(conexao, cnpj, key=filtros.chave, number=filtros.numero, series=filtros.serie) if filtros.situacao != 'autorizada' else ()
    finally:
        conexao.close()

    return ResultadoEventos(
        eventos=tuple(replace(_evento_da_linha(linha), ctes=ctes.get(linha[0], ()), cancelada=linha[0] in cancelled) for linha in cancellable_rows(linhas)),
        total=total,
        pagina=pagina_efetiva,
        por_pagina=por_pagina,
        notas_canceladas=cancelled_notes,
    )


def consultar_eventos_exportacao(
    database_path: Path,
    cnpj: str,
    filtros: FiltrosEventos,
    *,
    password: str | None = None,
    limite: int = 200_000,
) -> tuple[EventoNota, ...]:
    """Retorna todos os eventos filtrados para exportação."""
    conexao = abrir_banco(database_path, password, somente_leitura=True)
    try:
        tem_informacoes = _tem_informacoes_nfe(conexao)
        where, parametros = _where_eventos(cnpj, filtros, tem_informacoes, cancellation_condition(conexao, 'manifestacoes'))
        campos_nota = _campos_nota(tem_informacoes) + ", " + _campos_correcao(conexao)
        total = int(
            conexao.execute(
                f"SELECT COUNT(*) FROM manifestacoes WHERE {where}",
                parametros,
            ).fetchone()[0]
        )
        if total > limite:
            raise ValueError(
                f"A consulta retornou {total:,} eventos. "
                "Refine os filtros antes de exportar."
            )

        linhas = conexao.execute(
            f"""
            SELECT
                chave,
                codigo,
                descricao,
                data_evento,
                protocolo,
                nsu,
                recebido_em,
                {campos_nota}
            FROM manifestacoes
            WHERE {where}
            ORDER BY data_evento DESC, CAST(nsu AS INTEGER) DESC, id DESC
            """,
            parametros,
        ).fetchall()
        ctes = linked_ctes(conexao, cnpj, [line[0] for line in linhas])
        cancelled = cancelled_keys(conexao, cnpj, [line[0] for line in linhas])
    finally:
        conexao.close()

    return tuple(replace(_evento_da_linha(linha), ctes=ctes.get(linha[0], ()), cancelada=linha[0] in cancelled) for linha in cancellable_rows(linhas))


def consultar_numero_nota(
    database_path: Path,
    cnpj: str,
    numero: int,
    *,
    password: str | None = None,
    limite: int = 200,
) -> tuple[EventoNota, ...]:
    """Compatibilidade: busca eventos de uma única NF no banco local."""
    filtros = FiltrosEventos(numero=numero)
    resultado = consultar_eventos(
        database_path,
        cnpj,
        filtros,
        password=password,
        pagina=1,
        por_pagina=min(max(limite, 1), 500),
    )
    return resultado.eventos
