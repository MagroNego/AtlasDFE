"use client";

import { useEffect, useState, type FormEvent } from "react";

type Change = { chave: string; cancelada: boolean; usuario: string; motivo: string; alteradoEm: string };

export default function Situacao() {
  const [changes, setChanges] = useState<Change[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function refresh() {
    const response = await fetch("/api/admin/situacao");
    if (response.status === 401) { location.href = "/"; return; }
    if (!response.ok) throw new Error("Não foi possível carregar o histórico.");
    setChanges(await response.json());
  }

  useEffect(() => { void refresh().catch(() => setMessage("Não foi possível carregar o histórico.")); }, []);

  async function apply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    setBusy(true);
    setMessage("");
    try {
      const session = await fetch("/api/sessao");
      const { autenticado, token } = await session.json();
      if (!autenticado) { location.href = "/"; return; }
      const response = await fetch("/api/admin/situacao", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-TOKEN": token },
        body: JSON.stringify({ nota: fields.get("nota"), cancelada: fields.get("acao") === "cancelar", motivo: fields.get("motivo") }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.erro ?? "Não foi possível atualizar a situação.");
      }
      form.reset();
      await refresh();
      setMessage("Situação local registrada.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Não foi possível atualizar a situação.");
    } finally { setBusy(false); }
  }

  return <main>
    <header><a className="marca" href="/">Atlas <span>DFE</span></a><a href="/admin">Fila NSU</a></header>
    <section className="painel">
      <h1>Situação manual de NF-e</h1>
      <form onSubmit={apply}>
        <label>Número ou chave da nota<input name="nota" maxLength={44} inputMode="numeric" required /></label>
        <fieldset>
          <legend>Ação local</legend>
          <label className="inline-check"><input type="radio" name="acao" value="cancelar" defaultChecked /> Marcar cancelada</label>
          <label className="inline-check"><input type="radio" name="acao" value="remover" /> Remover marcação</label>
        </fieldset>
        <label>Motivo<input name="motivo" maxLength={500} required /></label>
        <button disabled={busy}>Registrar</button>
      </form>
      {message && <p role="status">{message}</p>}
      {changes.map((change, index) => <article className="nota" key={index}>
        <code>{change.chave}</code>
        <p>{change.cancelada ? "Marcação aplicada" : "Marcação removida"} · {change.usuario}</p>
        <p>{change.motivo}</p>
        <time dateTime={change.alteradoEm}>{new Date(change.alteradoEm).toLocaleString("pt-BR")}</time>
      </article>)}
    </section>
  </main>;
}
