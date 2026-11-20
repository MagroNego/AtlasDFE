"use client";

import { useState } from "react";
import { useAtlasSession } from "./atlas-frame";

export function ExportarArquivo({ href, children }: { href: string; children: string }) {
  const { refreshSession } = useAtlasSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function download() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(href);
      if (response.status === 401) { await refreshSession(); return; }
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.erro ?? "Não foi possível gerar o arquivo.");
      }
      const disposition = response.headers.get("content-disposition") ?? "";
      const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
      const filename = encoded ? decodeURIComponent(encoded)
        : disposition.match(/filename="?([^";]+)/i)?.[1] ?? "emissao.xlsx";
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Não foi possível gerar o arquivo.");
    } finally { setBusy(false); }
  }
  return <div className="exportacao-arquivo">
    <button type="button" className="secundario" onClick={() => void download()} disabled={busy} aria-busy={busy}>
      {busy ? "Gerando arquivo…" : children}
    </button>
    {error && <p className="erro" role="alert">{error}</p>}
  </div>;
}
