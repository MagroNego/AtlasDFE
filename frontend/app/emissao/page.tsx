"use client";

import { useEffect, useState, type FormEvent } from "react";

type Note = { chave: string; numero: number; serie: number; emissao: string; destinatario: string; valor: number; itens: number; cancelada: boolean };
type Report = { notas: Note[]; mais: boolean };
const currency = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

export default function Emissao() {
  const [report, setReport] = useState<Report>();
  const [filters, setFilters] = useState("");
  const [page, setPage] = useState(1);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<{ chave: string; items: Record<string, string | number>[] }>();

  async function read(query: string, next: number) {
    const params = new URLSearchParams(query);
    params.set("pagina", String(next));
    const response = await fetch(`/api/emissao?${params}`);
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
    void read("", 1).catch(() => setMessage("Não foi possível carregar a Emissão."));
  }, []);

  async function action(run: () => Promise<void>) {
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
      const session = await fetch("/api/sessao");
      if (!session.ok) throw new Error("Sessão indisponível.");
      const { token } = await session.json();
      const response = await fetch("/api/emissao/xml", { method: "POST", body: payload, headers: { "X-CSRF-TOKEN": token } });
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

  return <main>
    <header><a className="marca" href="/">Atlas <span>DFE</span></a><a href="/">Consulta</a></header>
    <section className="painel">
      <h1>Emissão</h1>
      <form onSubmit={upload}>
        <label>Importar XML ou ZIP<input name="arquivos" type="file" accept=".xml,.zip" multiple required /></label>
        <button disabled={busy}>Importar</button>
      </form>
      <form onSubmit={search}>
        <label>Nota, chave, empresa ou item<input name="busca" maxLength={200} /></label>
        <div className="emissao-filtros">
          <label>Emissão de<input type="date" name="inicio" /></label>
          <label>Até<input type="date" name="fim" /></label>
          <label>Situação<select name="situacao"><option value="">Todas</option><option value="autorizada">Autorizada</option><option value="cancelada">Cancelada</option></select></label>
        </div>
        <button disabled={busy}>Pesquisar</button> <a href={`/api/emissao/excel?${filters}`}>Excel</a>
      </form>
      {message && <p role="status">{message}</p>}
      {report?.notas.length === 0 && <p>Nenhuma NF-e encontrada.</p>}
      {report?.notas.map(note => <article className="nota" key={note.chave}>
        <h2>NF-e {note.numero} <small>Série {note.serie}</small></h2>
        <code>{note.chave}</code>
        <p>{note.emissao.split("-").reverse().join("/")} · {note.destinatario} · {currency.format(note.valor)} · {note.cancelada ? "Cancelada" : "Autorizada"}</p>
        <button className="secundario" disabled={busy} onClick={() => void action(async () => {
          const response = await fetch(`/api/emissao/${note.chave}/itens`);
          if (!response.ok) throw new Error("Não foi possível abrir os itens.");
          setDetail({ chave: note.chave, items: await response.json() });
        })}>Itens ({note.itens})</button>
        {detail?.chave === note.chave && <div className="emissao-tabela">
          <a href={`/api/emissao/${note.chave}/itens/excel`}>Excel dos itens</a>
          <table><thead><tr>{Object.keys(detail.items[0] ?? {}).map(field => <th key={field}>{field}</th>)}</tr></thead>
            <tbody>{detail.items.map((item, index) => <tr key={index}>{Object.keys(detail.items[0] ?? {}).map(field => <td key={field}>{item[field]}</td>)}</tr>)}</tbody></table>
        </div>}
      </article>)}
      {report && (page > 1 || report.mais) && <nav>
        <button disabled={busy || page === 1} onClick={() => void action(() => read(filters, page - 1))}>Anterior</button>
        <span>Página {page}</span>
        <button disabled={busy || !report.mais} onClick={() => void action(() => read(filters, page + 1))}>Próxima</button>
      </nav>}
    </section>
  </main>;
}
