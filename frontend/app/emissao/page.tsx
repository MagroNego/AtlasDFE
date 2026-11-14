"use client";

import { Fragment, useEffect, useState, type FormEvent } from "react";
import { useAtlasSession } from "../atlas-frame";

type Note = { chave: string; numero: number; serie: number; emissao: string; destinatario: string; valor: number; itens: number; cancelada: boolean };
type Report = { notas: Note[]; mais: boolean };
const currency = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

export default function Emissao() {
  const { session } = useAtlasSession();
  const [report, setReport] = useState<Report>();
  const [filters, setFilters] = useState("");
  const [page, setPage] = useState(1);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(true);
  const [detail, setDetail] = useState<{ chave: string; items: Record<string, string | number>[] }>();

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

  function openItems(chave: string) {
    if (detail?.chave === chave) {
      setDetail(undefined);
      return;
    }
    void action(async () => {
      const response = await fetch(`/api/emissao/${chave}/itens`);
      if (response.status === 401) {
        location.href = "/";
        return;
      }
      if (!response.ok) throw new Error("Não foi possível abrir os itens.");
      setDetail({ chave, items: await response.json() });
    });
  }

  const columns = Object.keys(detail?.items[0] ?? {});

  return <section className="painel">
    <h1>Emissão</h1>
    <form onSubmit={search}>
      <label>Nota, chave, empresa ou item<input name="busca" maxLength={200} /></label>
      <div className="emissao-filtros">
        <label>Emissão de<input type="date" name="inicio" /></label>
        <label>Até<input type="date" name="fim" /></label>
        <label>Situação<select name="situacao"><option value="">Todas</option><option value="autorizada">Autorizada</option><option value="cancelada">Cancelada</option></select></label>
      </div>
      <div className="form-acoes">
        <button disabled={busy}>Pesquisar</button>
        <a className="acao" href={`/api/emissao/excel?${filters}`}>Exportar Excel</a>
      </div>
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
              <th scope="col" role="columnheader">Destinatário</th><th scope="col" role="columnheader">Valor</th><th scope="col" role="columnheader">Situação</th><th scope="col" role="columnheader">Itens</th></tr></thead>
            <tbody role="rowgroup">{report.notas.map(note => {
              const expanded = detail?.chave === note.chave;
              return <Fragment key={note.chave}>
                <tr role="row">
                  <th scope="row" role="rowheader">{note.numero}<small>Série {note.serie}</small></th>
                  <td role="cell"><code>{note.chave}</code></td>
                  <td role="cell" className="numero">{note.emissao.split("-").reverse().join("/")}</td>
                  <td role="cell">{note.destinatario}</td>
                  <td role="cell" className="numero">{currency.format(note.valor)}</td>
                  <td role="cell">{note.cancelada ? <span className="selo selo-cancelado">Cancelada</span> : "Autorizada"}</td>
                  <td role="cell"><button type="button" className="secundario compacto" disabled={busy} aria-expanded={expanded}
                    aria-controls={expanded ? `itens-${note.chave}` : undefined}
                    onClick={() => openItems(note.chave)}>Itens ({note.itens})</button></td>
                </tr>
                {detail?.chave === note.chave && <tr role="row" className="linha-detalhe" id={`itens-${note.chave}`}>
                  <td role="cell" colSpan={7}>
                    <a className="acao compacto" href={`/api/emissao/${note.chave}/itens/excel`}>Exportar Excel dos itens</a>
                    {columns.length > 0 ? <div className="tabela-rolagem tabela-itens" tabIndex={0} role="region" aria-label="Itens da NF-e">
                      <table className="tabela">
                        <thead><tr>{columns.map(field => <th key={field} scope="col">{field}</th>)}</tr></thead>
                        <tbody>{detail.items.map((item, index) => <tr key={index}>
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
