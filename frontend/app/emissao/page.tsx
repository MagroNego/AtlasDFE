"use client";

import { Fragment, useEffect, useState, type FormEvent } from "react";
import { useAtlasSession } from "../atlas-frame";
import { EventosNota, type DetalheNota, type ResultadoConsulta } from "../eventos-nota";

type Note = { chave: string; numero: number; serie: number; emissao: string; destinatario: string; valor: number; itens: number; cancelada: boolean };
type Report = { notas: Note[]; mais: boolean };
type Detail = { chave: string } & (
  { tipo: "itens"; items: Record<string, string | number>[] }
  | { tipo: "eventos"; eventos: DetalheNota | null }
);
const currency = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

export default function Emissao() {
  const { session } = useAtlasSession();
  const [report, setReport] = useState<Report>();
  const [filters, setFilters] = useState("");
  const [page, setPage] = useState(1);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(true);
  const [detail, setDetail] = useState<Detail>();

  async function read(query: string, next: number, signal?: AbortSignal) {
    const params = new URLSearchParams(query);
    params.set("pagina", String(next));
    const response = await fetch(`/api/emissao?${params}`, { signal });
    if (response.status === 401) {
      location.href = "/";
      return;
    }
    if (!response.ok) throw new Error("Não foi possível carregar a Emissão.");
    setReport(await response.json());
    setFilters(query);
    setPage(next);
    setDetail(undefined);
  }

  useEffect(() => {
    const controller = new AbortController();
    void read("", 1, controller.signal).catch(() => {
      if (!controller.signal.aborted) setMessage("Não foi possível carregar a Emissão.");
    }).finally(() => {
      if (!controller.signal.aborted) setBusy(false);
    });
    return () => controller.abort();
  }, []);

  async function action(run: () => Promise<void>) {
    if (busy) return;
    setMessage("");
    setBusy(true);
    try { await run(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Falha ao processar a solicitação."); }
    finally { setBusy(false); }
  }

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const params = new URLSearchParams();
    for (const [name, value] of new FormData(event.currentTarget))
      if (typeof value === "string" && value.trim()) params.set(name, value.trim());
    void action(() => read(params.toString(), 1));
  }

  function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = new FormData(form);
    void action(async () => {
      const response = await fetch("/api/emissao/xml", {
        method: "POST", body: payload, headers: { "X-CSRF-TOKEN": session.token },
      });
      if (response.status === 401) {
        location.href = "/";
        return;
      }
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.erro ?? "Não foi possível importar o lote.");
      await read(filters, 1);
      form.reset();
      setMessage(`Importados: ${result.importadas}. Já existentes: ${result.existentes}.`);
    });
  }

  function openDetail(chave: string, tipo: Detail["tipo"]) {
    if (detail?.chave === chave && detail.tipo === tipo) {
      setDetail(undefined);
      return;
    }
    void action(async () => {
      const url = tipo === "itens" ? `/api/emissao/${chave}/itens`
        : `/api/consulta?busca=${encodeURIComponent(chave)}`;
      const response = await fetch(url);
      if (response.status === 401) {
        location.href = "/";
        return;
      }
      if (!response.ok) throw new Error(`Não foi possível abrir os ${tipo}.`);
      if (tipo === "itens") {
        setDetail({ chave, tipo, items: await response.json() });
      } else {
        const result: ResultadoConsulta = await response.json();
        setDetail({ chave, tipo, eventos: result.notas.find(note => note.chave === chave)?.detalhe ?? null });
      }
    });
  }

  const columns = Object.keys(detail?.tipo === "itens" ? detail.items[0] ?? {} : {});
  const applied = new URLSearchParams(filters);
  const filterCount = Number(applied.has("inicio") || applied.has("fim")) + Number(applied.has("situacao"));

  return <section className="tela-fiscal tela-emissao">
    <div className="emissao-cabecalho">
      <h1>Emissão</h1>
      <a className="emissao-exportar" href={`/api/emissao/excel?${filters}`}>Exportar Excel</a>
    </div>
    <form className="emissao-form" onSubmit={search}>
      <label htmlFor="emissao-busca">Nota, chave, empresa ou item</label>
      <div className="linha-busca">
        <input id="emissao-busca" name="busca" maxLength={200} />
        <button disabled={busy}>Pesquisar</button>
      </div>
      <details className="emissao-opcoes">
        <summary>{filterCount ? `Filtros (${filterCount})` : "Filtros"}</summary>
        <div className="emissao-filtros">
          <label>Emissão de<input type="date" name="inicio" /></label>
          <label>Até<input type="date" name="fim" /></label>
          <label>Situação<select name="situacao"><option value="">Todas</option><option value="autorizada">Autorizada</option><option value="cancelada">Cancelada</option></select></label>
        </div>
      </details>
    </form>
    {session.administrador && <details className="form-importacao">
      <summary>Importar XML ou ZIP</summary>
      <form onSubmit={upload}>
        <label>Arquivos de emissão<input name="arquivos" type="file" accept=".xml,.zip" multiple required /></label>
        <button disabled={busy}>Importar</button>
      </form>
    </details>}
    {busy && <p className={report ? "sr-only" : "mensagem"} role="status">Processando Emissão…</p>}
    {message && <p className="mensagem" role="status">{message}</p>}
    {report && <div className="resultado" aria-busy={busy}>
      {report.notas.length === 0 ? <p className="mensagem">Nenhuma NF-e encontrada.</p>
        : <div key={`${filters}:${page}`} className="tabela-rolagem" tabIndex={0} role="region" aria-label="Notas de emissão">
          <table className="tabela tabela-emissao" role="table">
            <thead role="rowgroup"><tr role="row"><th scope="col" role="columnheader">NF-e / Série</th><th scope="col" role="columnheader">Chave de acesso</th><th scope="col" role="columnheader">Emissão</th>
              <th scope="col" role="columnheader">Destinatário</th><th scope="col" role="columnheader">Valor</th><th scope="col" role="columnheader">Situação</th><th scope="col" role="columnheader">Ações</th></tr></thead>
            <tbody role="rowgroup">{report.notas.map(note => {
              const activeDetail = detail?.chave === note.chave ? detail : undefined;
              const detailId = `emissao-detalhe-${note.chave}`;
              return <Fragment key={note.chave}>
                <tr role="row" className={activeDetail ? "emissao-nota aberta" : "emissao-nota"}>
                  <th scope="row" role="rowheader">{note.numero}<small>Série {note.serie}</small></th>
                  <td role="cell"><code>{note.chave}</code></td>
                  <td role="cell" className="numero">{note.emissao.split("-").reverse().join("/")}</td>
                  <td role="cell">{note.destinatario}</td>
                  <td role="cell" className="numero">{currency.format(note.valor)}</td>
                  <td role="cell">{note.cancelada ? <span className="selo selo-cancelado">Cancelada</span> : "Autorizada"}</td>
                  <td role="cell">
                    <div className="emissao-acoes">
                      <button type="button" className="secundario compacto" disabled={busy} aria-expanded={activeDetail?.tipo === "itens"}
                        aria-controls={activeDetail?.tipo === "itens" ? detailId : undefined}
                        onClick={() => openDetail(note.chave, "itens")}>Itens ({note.itens})</button>
                      <button type="button" className="secundario compacto" disabled={busy} aria-expanded={activeDetail?.tipo === "eventos"}
                        aria-controls={activeDetail?.tipo === "eventos" ? detailId : undefined}
                        aria-label={`Eventos da NF-e ${note.numero}, série ${note.serie}`}
                        onClick={() => openDetail(note.chave, "eventos")}>Eventos</button>
                    </div>
                  </td>
                </tr>
                {activeDetail?.tipo === "eventos" && <tr role="row" className="linha-detalhe" id={detailId}>
                  <td role="cell" colSpan={7}>
                    {activeDetail.eventos ? <EventosNota detalhe={activeDetail.eventos} />
                      : <p role="status">Esta NF-e ainda não consta no histórico consultado pelo Atlas.</p>}
                  </td>
                </tr>}
                {activeDetail?.tipo === "itens" && <tr role="row" className="linha-detalhe" id={detailId}>
                  <td role="cell" colSpan={7}>
                    <a className="acao compacto" href={`/api/emissao/${note.chave}/itens/excel`}>Exportar Excel dos itens</a>
                    {columns.length > 0 ? <div className="tabela-rolagem tabela-itens" tabIndex={0} role="region" aria-label="Itens da NF-e">
                      <table className="tabela">
                        <thead><tr>{columns.map(field => <th key={field} scope="col">{field}</th>)}</tr></thead>
                        <tbody>{activeDetail.items.map((item, index) => <tr key={index}>
                          {columns.map(field => <td key={field}>{item[field]}</td>)}
                        </tr>)}</tbody>
                      </table>
                    </div> : <p>Nenhum item encontrado.</p>}
                  </td>
                </tr>}
              </Fragment>;
            })}</tbody>
          </table>
        </div>}
      {(page > 1 || report.mais) && <nav className="paginacao" aria-label="Páginas de emissão">
        <button className="secundario" disabled={busy || page === 1} onClick={() => void action(() => read(filters, page - 1))}>Anterior</button>
        <span>Página {page}</span>
        <button className="secundario" disabled={busy || !report.mais} onClick={() => void action(() => read(filters, page + 1))}>Próxima</button>
      </nav>}
    </div>}
  </section>;
}
