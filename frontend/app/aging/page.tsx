"use client";

import { useState, type FormEvent } from "react";
import { useAtlasSession } from "../atlas-frame";

type Preview = { aba: string; total: number; colunas: string[]; linhas: string[][] };

function download(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function Aging() {
  const { session, refreshSession } = useAtlasSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [diagnostic, setDiagnostic] = useState("");
  const [preview, setPreview] = useState<Preview>();
  const [converted, setConverted] = useState(false);

  async function convert(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const submitter = (event.nativeEvent as SubmitEvent).submitter;
    const inspect = (submitter as HTMLButtonElement | null)?.value !== "excel";
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    setDiagnostic("");
    setConverted(false);
    if (inspect) setPreview(undefined);
    try {
      const response = await fetch(inspect ? "/api/aging?previa=true" : "/api/aging", {
        method: "POST", body: form, headers: { "X-CSRF-TOKEN": session.token },
      });
      if (response.status === 401) { await refreshSession(); return; }
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        setDiagnostic(result.diagnostico ?? "");
        throw new Error(result.erro ?? "Não foi possível converter o Aging.");
      }
      if (inspect) setPreview(await response.json());
      else {
        download(await response.blob(), "aging.xlsx");
        setConverted(true);
      }
    } catch (error) {
      setError(error instanceof Error ? error.message : "Não foi possível converter o Aging.");
    } finally { setBusy(false); }
  }

  return <section className="tela-fiscal">
    <div className="titulo-fiscal"><h1>Aging List</h1></div>
    <section className="painel-fiscal">
      <form onSubmit={convert}>
        <label>Arquivo de vencimentos do Infor LN
          <input required name="arquivo" type="file" accept=".txt,.xlsx,.pdf" aria-describedby="aging-formatos"
            disabled={busy} onChange={() => { setPreview(undefined); setDiagnostic(""); setError(""); setConverted(false); }} />
        </label>
        <p id="aging-formatos" className="muted">TXT, XLSX ou PDF de até 5 MiB.</p>
        <div className="form-acoes">
          <button value="previa" disabled={busy}>{busy ? "Processando…" : "Ver prévia"}</button>
          <button value="excel" className="secundario" disabled={busy}>Converter para Excel</button>
        </div>
      </form>
      {error && <p role="alert" className="erro">{error}</p>}
      {diagnostic && <button type="button" className="secundario"
        onClick={() => download(new Blob([diagnostic], { type: "text/plain;charset=utf-8" }), "aging_diagnostico.txt")}>Baixar diagnóstico TXT</button>}
      {converted && <p role="status" className="mensagem">Conversão concluída. Download iniciado.</p>}
      {preview && <div className="resultado">
        <h2>Prévia · {preview.aba}</h2>
        <p className="muted">{preview.linhas.length} de {preview.total} linhas</p>
        <div className="tabela-rolagem" tabIndex={0}><table className="tabela">
          <thead><tr>{preview.colunas.map((value, index) => <th scope="col" key={index}>{value}</th>)}</tr></thead>
          <tbody>{preview.linhas.map((row, index) => <tr key={index}>
            {row.map((value, col) => <td key={col}>{value}</td>)}
          </tr>)}</tbody>
        </table></div>
      </div>}
    </section>
  </section>;
}
