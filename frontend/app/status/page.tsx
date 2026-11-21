"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useAtlasSession } from "../atlas-frame";

type Entry = {
  em: string;
  nsu: number | null;
  recuperacao: boolean;
  tipo: "envio" | "resposta";
  cstat: number | null;
  cursor: number | null;
  documentos: number | null;
};

type Integrity = {
  verificadoEm: string;
  publicado: boolean;
  medidas: { nome: string; sqlite: number; postgres: number }[];
};

type Status = {
  integridade: Integrity | null;
  bancoDisponivel: boolean;
  consultadoEm: string;
  snapshot: {
    ultNsu: number | null;
    maxNsu: number | null;
    pausaAte: string | null;
    ultimaGravacao: string | null;
    ultimaReserva: string | null;
    respostasPendentes: number;
    pendenteDesde: string | null;
    filaPendente: number;
    emExecucao: boolean;
    atividade: Entry[];
  };
  agenda: {
    habilitada: boolean;
    inicio: string | null;
    fim: string | null;
    naJanela: boolean;
    emPausa: boolean;
    proxima: string | null;
    horarios: string[];
    dias: number[];
    diagnostico: string | null;
  };
};

const brasilia = new Intl.DateTimeFormat("pt-BR", {
  timeZone: "America/Sao_Paulo", dateStyle: "short", timeStyle: "medium",
});
const days = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"];
const date = (value: string | null) => value ? brasilia.format(new Date(value)) : "—";
const nsu = (value: number | null) => value === null ? "—" : String(value).padStart(15, "0");

function result(entry: Entry) {
  if (entry.tipo === "envio") return "Tentativa registrada";
  switch (entry.cstat) {
    case null: return "Resposta armazenada, processamento pendente";
    case 137: return entry.recuperacao ? "137 · NSU indisponível" : "137 · Nenhum documento disponível";
    case 138: return "138 · Documentos localizados";
    case 656: return "656 · Consumo indevido";
    default: return `${entry.cstat} · Retorno da SEFAZ`;
  }
}

const amount = new Intl.NumberFormat("pt-BR");
function MigrationChart({ report }: { report: Integrity | null }) {
  if (!report) return <p className="muted">Aguardando a primeira conferência da migração SQLite → PostgreSQL.</p>;
  const counts = report.medidas.filter(row => !row.nome.startsWith("Chaves") && !row.nome.includes("NSU"));
  const checks = report.medidas.filter(row => !counts.includes(row));
  const max = Math.max(1, ...counts.map(row => Math.max(row.sqlite, row.postgres)));
  return <>
    <p role="status">{report.publicado ? "Conferência concluída" : "Diferenças detectadas. A cópia foi revertida."} · {date(report.verificadoEm)}</p>
    <div className="integridade-legenda"><span>SQLite</span><span>PostgreSQL</span></div>
    <ul className="integridade-grafico" aria-label="Comparação dos totais do SQLite e PostgreSQL">
      {counts.map(row => {
        return <li key={row.nome}>
          <span>{row.nome}</span>
          <div className="integridade-barras">
            {[row.sqlite, row.postgres].map((value, index) => <div key={index} className="integridade-linha">
              <span className={index ? "pg" : "sqlite"} style={{ width: `${(value / max) * 100}%` }} aria-hidden="true" />
              <small aria-label={`${index ? "PostgreSQL" : "SQLite"} ${value}`}>{amount.format(value)}</small>
            </div>)}
          </div>
          {row.sqlite !== row.postgres && <small className="integridade-diferenca">Diferença de {amount.format(row.postgres - row.sqlite)}</small>}
        </li>;
      })}
    </ul>
    <details>
      <summary>Chaves e NSU</summary>
      <ul>{checks.map(row => <li key={row.nome}>{row.nome} · {row.nome.startsWith("Chaves")
        ? row.sqlite === row.postgres ? "Conferidas" : "Divergentes"
        : `SQLite ${nsu(row.sqlite)} · PostgreSQL ${row.postgres < 0 ? "—" : nsu(row.postgres)}`}</li>)}</ul>
    </details>
    <small>Retrato da última cópia comparada.</small>
  </>;
}

export default function SefazStatus() {
  const { session, refreshSession } = useAtlasSession();
  const [status, setStatus] = useState<Status>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      setBusy(true);
      try {
        const response = await fetch("/api/sefaz/status", { cache: "no-store", signal: controller.signal });
        if (response.status === 401 || response.status === 403) {
          setStatus(undefined);
          setError("A sessão precisa ser revalidada.");
          await refreshSession();
          return;
        }
        if (!response.ok) throw new Error();
        const data: Status = await response.json();
        if (!controller.signal.aborted) {
          setStatus(data);
          setError("");
        }
      } catch {
        if (!controller.signal.aborted) {
          setStatus(undefined);
          setError("Não foi possível consultar o Status SEFAZ. Tente atualizar.");
        }
      } finally {
        if (!controller.signal.aborted) {
          setBusy(false);
          timer = setTimeout(load, 30_000);
        }
      }
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [refresh, refreshSession]);

  const snapshot = status?.snapshot;
  const agenda = status?.agenda;
  let execution = "Sem execução em andamento";
  if (snapshot?.emExecucao) execution = "Sincronização em andamento";
  else if (snapshot?.respostasPendentes) execution = "Processamento de respostas pendente";
  else if (snapshot?.ultNsu === null) execution = "Cursor ainda não registrado";

  return <section className="tela-fiscal">
    <div className="titulo-fiscal">
      <h1>Status SEFAZ</h1>
      <button type="button" className="secundario" disabled={busy}
        onClick={() => setRefresh(value => value + 1)}>{busy ? "Atualizando…" : "Atualizar status"}</button>
    </div>
    {error && <p role="alert" className="erro">{error}</p>}
    {!status && !error && <p role="status" className="mensagem">Consultando Status SEFAZ…</p>}
    {status && snapshot && agenda && <>
      <section className="painel-fiscal">
        <div className="sefaz-cabecalho">
          <h2>Coleta de documentos</h2>
          <small>Atualizado em {date(status.consultadoEm)} · Brasília</small>
        </div>
        <dl className="sefaz-dados">
          <div><dt>Banco de dados</dt><dd>{status.bancoDisponivel ? "Disponível" : "Indisponível"}</dd></div>
          <div><dt>Coleta</dt><dd>{execution}</dd></div>
          <div><dt>Último NSU</dt><dd><code>{nsu(snapshot.ultNsu)}</code></dd></div>
          <div><dt>NSU máximo</dt><dd><code>{nsu(snapshot.maxNsu)}</code></dd></div>
          <div><dt>Última gravação de resposta</dt><dd>{date(snapshot.ultimaGravacao)}</dd></div>
          <div><dt>Respostas pendentes</dt><dd>{snapshot.respostasPendentes}
            {snapshot.pendenteDesde && <small> · Mais antiga em {date(snapshot.pendenteDesde)}</small>}</dd></div>
          <div><dt>Fila de recuperação</dt><dd>{snapshot.filaPendente} pendente(s)
            {session.administrador && <> · <Link href="/admin">Abrir fila</Link></>}</dd></div>
        </dl>
      </section>
      <div className="sefaz-paineis">
        <section className="painel-fiscal resultado integridade-painel">
          <h2>Integridade da migração</h2>
          <MigrationChart report={status.integridade} />
        </section>
        <section className="painel-fiscal resultado">
          <h2>Janela e agenda</h2>
          {agenda.diagnostico && <p className="erro">{agenda.diagnostico}</p>}
          <dl className="sefaz-dados">
            <div><dt>Janela de consulta</dt><dd>{agenda.inicio && agenda.fim ?
              `${agenda.inicio} a ${agenda.fim} · ${agenda.naJanela ? "Aberta" : "Fechada"}` : "—"}</dd></div>
            <div><dt>Intervalo de espera</dt><dd>{agenda.emPausa ? `Até ${date(snapshot.pausaAte)}` : "Sem pausa ativa"}</dd></div>
            <div><dt>Agenda automática</dt><dd>{agenda.habilitada ? "Ativada" : "Desativada"}</dd></div>
            {agenda.habilitada && <>
              <div><dt>Dias e horários configurados</dt><dd>{agenda.horarios.length ? <>
                {agenda.dias.map(day => days[day]).join(", ")}<br />{agenda.horarios.join(", ")}
              </> : "—"}</dd></div>
              <div><dt>Último horário reservado</dt><dd>{date(snapshot.ultimaReserva)}</dd></div>
              <div><dt>Próximo horário elegível (estimativa)</dt><dd>{date(agenda.proxima)}</dd></div>
            </>}
          </dl>
        </section>
      </div>
      <section className="painel-fiscal resultado">
        <h2>Atividade recente</h2>
        {snapshot.atividade.length === 0 ? <p className="muted">Nenhuma tentativa ou resposta registrada.</p>
          : <div className="tabela-rolagem sefaz-atividade" tabIndex={0} role="region" aria-label="Atividade SEFAZ">
            <table className="tabela">
              <thead><tr><th scope="col">Data e hora</th><th scope="col">Consulta</th><th scope="col">NSU enviado</th>
                <th scope="col">Resultado</th><th scope="col">Cursor</th><th scope="col">Documentos</th></tr></thead>
              <tbody>{snapshot.atividade.map((entry, index) => <tr key={index}>
                <td>{date(entry.em)}</td><td>{entry.recuperacao ? "consNSU" : "distNSU"}</td><td><code>{nsu(entry.nsu)}</code></td>
                <td>{result(entry)}</td><td><code>{nsu(entry.cursor)}</code></td><td>{entry.documentos ?? "—"}</td>
              </tr>)}</tbody>
            </table>
          </div>}
      </section>
    </>}
  </section>;
}
