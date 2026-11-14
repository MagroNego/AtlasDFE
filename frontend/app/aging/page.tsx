"use client";

import { useState, type FormEvent } from "react";
import { useAtlasSession } from "../atlas-frame";

export default function Aging() {
  const { session, refreshSession } = useAtlasSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [converted, setConverted] = useState(false);
  async function convert(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    setConverted(false);
    try {
      const response = await fetch("/api/aging", {
        method: "POST", body: form, headers: { "X-CSRF-TOKEN": session.token },
      });
      if (response.status === 401) {
        await refreshSession();
        return;
      }
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.erro ?? "Não foi possível converter o Aging.");
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "aging.xlsx";
      link.click();
      setConverted(true);
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      setError(error instanceof Error ? error.message : "Não foi possível converter o Aging.");
    } finally {
      setBusy(false);
    }
  }

  return <section className="painel login">
    <h1>Aging List</h1>
    <form onSubmit={convert}>
      <label>Arquivo de vencimentos do Infor LN
        <input required name="arquivo" type="file" accept=".txt,.xlsx,.pdf" aria-describedby="aging-formatos" />
      </label>
      <p id="aging-formatos" className="muted">TXT, XLSX ou PDF de até 5 MiB.</p>
      <button disabled={busy}>{busy ? "Convertendo…" : "Converter para Excel"}</button>
    </form>
    {error && <p role="alert" className="erro">{error}</p>}
    {converted && <p role="status" className="mensagem">Conversão concluída. Download iniciado.</p>}
  </section>;
}
