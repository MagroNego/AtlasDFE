"use client";

import { useEffect, useState, type FormEvent } from "react";

type QueueItem = { nsu: number; high: boolean; status: string };

export default function Admin() {
  const [items, setItems] = useState<QueueItem[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function refresh() {
    const response = await fetch("/api/admin/fila-nsu");
    if (response.status === 401) { location.href = "/"; return; }
    if (!response.ok) throw new Error("Não foi possível consultar a fila.");
    setItems(await response.json());
  }

  useEffect(() => { void refresh().catch(() => setError("Não foi possível consultar a fila.")); }, []);

  async function update(nsu: string, alta: boolean, cancelar: boolean) {
    setError("");
    setBusy(true);
    try {
      const session = await fetch("/api/sessao");
      const { autenticado, token } = await session.json();
      if (!autenticado) { location.href = "/"; return; }
      const response = await fetch("/api/admin/fila-nsu", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-TOKEN": token },
        body: JSON.stringify({ nsu, alta, cancelar }),
      });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.erro ?? "Não foi possível atualizar a fila.");
      }
      await refresh();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Não foi possível atualizar a fila.");
    } finally { setBusy(false); }
  }

  function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    void update(String(data.get("nsu")), data.has("alta"), false);
  }

  return <main>
    <header><a className="marca" href="/">Atlas <span>DFE</span></a><nav><a href="/admin/contas">Contas e auditoria</a><a href="/admin/situacao">Situação manual</a><a href="/">Consulta</a></nav></header>
    <section className="painel">
      <h1>Fila de recuperação NSU</h1>
      <form onSubmit={add}>
        <label>NSU<input name="nsu" inputMode="numeric" maxLength={15} pattern="[0-9]{1,15}" required /></label>
        <label className="inline-check"><input type="checkbox" name="alta" /> Prioridade alta</label>
        <button disabled={busy}>Adicionar</button>
      </form>
      {error && <p role="alert" className="erro">{error}</p>}
      {items.length === 0 && <p>Nenhum NSU na fila.</p>}
      {items.map(item => <article className="nota" key={item.nsu}>
        <code>{String(item.nsu).padStart(15, "0")}</code>
        <p>{item.high ? "Alta" : "Normal"} · {item.status}</p>
        {item.status === "pendente" && <button className="secundario" disabled={busy}
          onClick={() => void update(String(item.nsu), item.high, true)}>Cancelar</button>}
      </article>)}
    </section>
  </main>;
}
