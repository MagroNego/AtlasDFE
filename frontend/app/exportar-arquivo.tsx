"use client";

import Link from "next/link";
import { useState } from "react";
import { useAtlasSession } from "./atlas-frame";

export function ExportarArquivo({ href, children }: { href: string; children: string }) {
  const { session, refreshSession } = useAtlasSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [queued, setQueued] = useState(false);

  async function request() {
    if (busy) return;
    setBusy(true);
    setError("");
    setQueued(false);
    try {
      const response = await fetch(href, { method: "POST", headers: { "X-CSRF-TOKEN": session.token } });
      if (response.status === 401) { await refreshSession(); return; }
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.erro ?? "Não foi possível solicitar o arquivo.");
      }
      setQueued(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Não foi possível solicitar o arquivo.");
    } finally { setBusy(false); }
  }

  return <div className="exportacao-arquivo">
    <button type="button" className="secundario" onClick={() => void request()} disabled={busy} aria-busy={busy}>
      {busy ? "Enviando pedido…" : children}
    </button>
    {queued && <p role="status">Pedido na fila. <Link href="/downloads">Ver Downloads</Link></p>}
    {error && <p className="erro" role="alert">{error}</p>}
  </div>;
}
