"use client";

import { useState, type FormEvent } from "react";

export default function Aging() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function convert(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const session = await fetch("/api/sessao");
      if (!session.ok) throw new Error("Sessão indisponível.");
      const { autenticado, token } = await session.json();
      if (!autenticado) {
        location.href = "/";
        return;
      }
      const response = await fetch("/api/aging", { method: "POST", body: form, headers: { "X-CSRF-TOKEN": token } });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.erro ?? "Não foi possível converter o Aging.");
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "aging.xlsx";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      setError(error instanceof Error ? error.message : "Não foi possível converter o Aging.");
    } finally {
      setBusy(false);
    }
  }

  return <main>
    <header><a className="marca" href="/">Atlas <span>DFE</span></a><a href="/">Consulta</a></header>
    <section className="painel login">
      <h1>Aging List</h1>
      <form onSubmit={convert}>
        <label>TXT, XLSX ou PDF de vencimentos do Infor LN<input required name="arquivo" type="file" accept=".txt,.xlsx,.pdf" /></label>
        <button disabled={busy}>{busy ? "Convertendo…" : "Converter para Excel"}</button>
      </form>
      {error && <p role="alert" className="erro">{error}</p>}
    </section>
  </main>;
}
