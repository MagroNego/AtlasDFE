"use client";

import Link from "next/link";
import { Fragment, useEffect, useState, type FormEvent } from "react";
import { useAtlasSession } from "../atlas-frame";
import { EventosNota, rotulosAcoes, type AcaoNota, type DetalheNota, type ResultadoConsulta } from "../eventos-nota";
import { ItensNota, type ItemNota } from "../itens-nota";
import { ExportarArquivo } from "../exportar-arquivo";

type Note = { chave: string; numero: number; serie: number; emissao: string; destinatario: string; valor: number; itens: number; cancelada: boolean; temCce: boolean; temCte: boolean };
type Report = { notas: Note[]; colunas?: string[]; linhas?: ItemNota[]; mais: boolean };
type Detail = { chave: string } & (
  { tipo: "itens"; items: ItemNota[] }
  | { tipo: Exclude<AcaoNota, "itens">; eventos: DetalheNota | null }
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
    const type = params.get("tipo") ?? "notas";
    const response = await fetch(`/api/emissao${type === "notas" ? "" : "/relatorio"}?${params}`, { signal });
    if (response.status === 401) {
      location.href = "/";
      return;
    }
    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      throw new Error(result.erro ?? "Não foi possível carregar a Emissão.");
    }
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
      if (!response.ok) throw new Error(result.id
        ? `Lote ${result.id} revertido. ${result.erros} erro(s). ${result.erro}`
        : result.erro ?? "Não foi possível importar o lote.");
      await read(filters, 1);
      form.reset();
      setMessage(`Lote ${result.id} · ${result.notas} nota(s), ${result.cancelamentos} cancelamento(s), ${result.cce} CC-e, ${result.duplicatas} duplicata(s).`);
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
      if (!response.ok) throw new Error(tipo === "eventos" || tipo === "itens"
        ? `Não foi possível abrir os ${tipo}.` : `Não foi possível abrir ${rotulosAcoes[tipo]}.`);
      if (tipo === "itens") {
        setDetail({ chave, tipo, items: await response.json() });
      } else {
        const result: ResultadoConsulta = await response.json();
        setDetail({ chave, tipo, eventos: result.notas.find(note => note.chave === chave)?.detalhe ?? null });
      }
    });
  }

  return <section className="tela-fiscal tela-emissao">
    <div className="titulo-fiscal">
      <h1>Emissão</h1>
      {session.administrador && <details className="form-importacao">
        <summary>Importar XML ou ZIP</summary>
        <form onSubmit={upload}>
          <label>Arquivos de emissão<input name="arquivos" type="file" accept=".xml,.zip" multiple required /></label>
          <div className="form-acoes"><button disabled={busy}>Importar</button>
            <Link href="/admin/importacoes" className="acao">Histórico de lotes</Link></div>
        </form>
      </details>}
    </div>
    <form className="filtros-fiscais painel-fiscal" onSubmit={search}
      onReset={() => void action(() => read("", 1))}>
      <h2>Vendas</h2>
      <div className="campos-fiscais campos-emissao">
        <label>Relatório<select name="tipo"><option value="notas">Notas emitidas</option><option value="itens">Itens e impostos</option><option value="retencoes">PIS/COFINS retidos</option></select></label>
        <label>Nota, chave, empresa ou item<input name="busca" maxLength={200} placeholder="NF, chave, destinatário ou item" /></label>
        <label>Situação<select name="situacao"><option value="">Todas</option><option value="autorizada">Autorizada</option><option value="cancelada">Cancelada</option></select></label>
        <label>Emissão de<input type="date" name="inicio" /></label>
        <label>Até<input type="date" name="fim" /></label>
        <label>Item<input name="item" type="search" maxLength={200} placeholder="Descrição, código ou NCM" /></label>
      </div>
      <div className="acoes-filtros">
        <button disabled={busy}>Pesquisar</button>
        <button type="reset" className="secundario" disabled={busy}>Limpar filtros</button>
      </div>
    </form>
    {busy && <p className={report ? "sr-only" : "mensagem"} role="status">Processando Emissão…</p>}
    {message && <p className="mensagem" role="status">{message}</p>}
    {report && <div className="resultado painel-fiscal" aria-busy={busy}>
      <div className="resultado-barra">
        <ExportarArquivo href={`/api/emissao/excel?${filters}`}>Exportar Excel</ExportarArquivo>
        <ExportarArquivo href={`/api/emissao/csv?${filters}`}>Exportar CSV</ExportarArquivo>
      </div>
      <p className="muted ajuda-exportacao">Excel com Notas, Itens, Retenções e Resumo. CSV do relatório consultado.</p>
      {report.linhas ? (report.linhas.length === 0 ? <p className="mensagem">Nenhum registro encontrado.</p>
        : <div className="tabela-rolagem tabela-relatorio" tabIndex={0} role="region" aria-label="Relatório de emissão">
          <table className="tabela"><thead><tr>{report.colunas?.map(field => <th key={field} scope="col">{field}</th>)}</tr></thead>
            <tbody>{report.linhas.map((row, index) => <tr key={index}>{report.colunas?.map(field =>
              <td key={field}>{field === "Data Emissao" ? String(row[field] ?? "").split("-").reverse().join("/") : row[field]}</td>)}</tr>)}</tbody>
          </table>
        </div>) : report.notas.length === 0 ? <p className="mensagem">Nenhuma NF-e encontrada.</p>
        : <div key={`${filters}:${page}`} className="tabela-rolagem" tabIndex={0} role="region" aria-label="Notas de emissão">
          <table className="tabela tabela-emissao" role="table">
            <thead role="rowgroup"><tr role="row"><th scope="col" role="columnheader">Ações</th><th scope="col" role="columnheader">NF-e</th><th scope="col" role="columnheader">Chave de acesso</th><th scope="col" role="columnheader">Emissão</th>
              <th scope="col" role="columnheader">Destinatário</th><th scope="col" role="columnheader">Valor</th><th scope="col" role="columnheader">Situação</th></tr></thead>
            <tbody role="rowgroup">{report.notas.map(note => {
              const activeDetail = detail?.chave === note.chave ? detail : undefined;
              const detailId = `emissao-detalhe-${note.chave}`;
              return <Fragment key={note.chave}>
                <tr role="row" className={activeDetail ? "emissao-nota aberta" : "emissao-nota"}>
                  <td role="cell">
                    <div className="acoes-nota emissao-acoes">
                      <button type="button" className="secundario compacto acao-nota" disabled={busy} aria-expanded={activeDetail?.tipo === "itens"}
                        aria-controls={activeDetail?.tipo === "itens" ? detailId : undefined}
                        aria-label={`Itens (${note.itens})`}
                        onClick={() => openDetail(note.chave, "itens")}>
                        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01" /></svg>
                        Itens
                      </button>
                      <button type="button" className="secundario compacto acao-nota" disabled={busy} aria-expanded={activeDetail?.tipo === "eventos"}
                        aria-controls={activeDetail?.tipo === "eventos" ? detailId : undefined}
                        aria-label={`Eventos da NF-e ${note.numero}, série ${note.serie}`}
                        onClick={() => openDetail(note.chave, "eventos")}>
                        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2h-3M9 2h6v4H9zM8 13l3 3 5-6" /></svg>
                        Eventos
                      </button>
                      {(["cce", "cte"] as const).map(tipo => (tipo === "cce" ? note.temCce : note.temCte) &&
                        <button key={tipo} type="button" className="secundario compacto acao-nota" disabled={busy}
                          aria-expanded={activeDetail?.tipo === tipo}
                          aria-controls={activeDetail?.tipo === tipo ? detailId : undefined}
                          aria-label={`${rotulosAcoes[tipo]} da NF-e ${note.numero}, série ${note.serie}`}
                          onClick={() => openDetail(note.chave, tipo)}>{rotulosAcoes[tipo]}</button>)}
                    </div>
                  </td>
                  <th scope="row" role="rowheader">{note.numero}</th>
                  <td role="cell"><code>{note.chave}</code></td>
                  <td role="cell" className="numero">{note.emissao.split("-").reverse().join("/")}</td>
                  <td role="cell">{note.destinatario}</td>
                  <td role="cell" className="numero">{currency.format(note.valor)}</td>
                  <td role="cell">{note.cancelada ? <span className="selo selo-cancelado">Cancelada</span> : "Autorizada"}</td>
                </tr>
                {activeDetail && activeDetail.tipo !== "itens" && <tr key={activeDetail.tipo} role="row" className="linha-detalhe" id={detailId}>
                  <td role="cell" colSpan={7}>
                    {activeDetail.eventos ? <EventosNota detalhe={activeDetail.eventos} tipo={activeDetail.tipo} />
                      : <p role="status">Esta NF-e ainda não consta no histórico consultado pelo Atlas.</p>}
                  </td>
                </tr>}
                {activeDetail?.tipo === "itens" && <tr role="row" className="linha-detalhe" id={detailId}>
                  <td role="cell" colSpan={7}>
                    <ItensNota key={note.chave} chave={note.chave} items={activeDetail.items} />
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
