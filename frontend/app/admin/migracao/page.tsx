"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { useAtlasSession } from "../../atlas-frame";

export default function Migracao() {
  const { session, refreshSession } = useAtlasSession();
  const [config, setConfig] = useState<{ cnpj: string; limiteMiB: number }>();
  const [file, setFile] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ error: boolean; text: string }>();

  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/admin/migracao", { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error();
      setConfig(await response.json());
    }).catch(() => {
      if (!controller.signal.aborted) setNotice({ error: true, text: "Não foi possível carregar os dados da migração." });
    });
    return () => controller.abort();
  }, []);

  async function migrate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const body = new FormData(form);
    if (!window.isSecureContext) {
      setNotice({ error: true, text: "Abra o Atlas por HTTPS ou localhost para enviar o banco." });
      return;
    }
    setBusy(true);
    setNotice(undefined);
    try {
      const response = await fetch("/api/admin/migracao", { method: "POST", body, headers: { "X-CSRF-TOKEN": session.token } });
      if (response.status === 401) { await refreshSession(); return; }
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.erro ?? "Não foi possível concluir a migração. Confira o Status SEFAZ antes de tentar de novo.");
      form.reset();
      setFile("");
      setNotice({ error: false, text: `${result.total} nota(s) publicadas após a conferência.` });
    } catch (error) {
      setNotice({ error: true, text: error instanceof Error ? error.message : "Não foi possível concluir a migração." });
    } finally { setBusy(false); }
  }

  return <section className="tela-fiscal">
    <div className="titulo-fiscal"><h1>Migração do banco</h1><Link href="/status" className="acao">Ver integridade</Link></div>
    <section className="painel-fiscal">
      <h2>SQLite → PostgreSQL</h2>
      {config && <p>CNPJ do destino <strong>{config.cnpj}</strong></p>}
      <p className="muted">Use uma cópia independente do SQLite ou SQLCipher antes de iniciar a coleta no Atlas novo. A operação substitui o histórico do CNPJ no PostgreSQL após a conferência.</p>
      <form onSubmit={migrate}>
        <fieldset disabled={busy || !config}>
          <label className="arquivo-seletor">
            <input name="arquivo" type="file" accept=".sqlite,.sqlite3,.db" required
              aria-label="Cópia SQLite" onChange={event => setFile(event.currentTarget.files?.[0]?.name ?? "")} />
            <span className="arquivo-icone" aria-hidden="true">↑</span>
            <span className="arquivo-texto"><strong>{file || "Selecionar cópia SQLite"}</strong>
              <small>Até {config?.limiteMiB ?? 60} MiB</small></span>
          </label>
          <label>Senha do SQLCipher, se houver<input name="senha" type="password" maxLength={256} autoComplete="off" /></label>
          <label>Digite MIGRAR para confirmar<input name="confirmacao" pattern="MIGRAR" required autoComplete="off" /></label>
          <button>{busy ? "Migrando e conferindo…" : "Migrar para PostgreSQL"}</button>
        </fieldset>
      </form>
      {busy && <p role="status">Mantenha esta página aberta até a conclusão.</p>}
      {notice && <p role={notice.error ? "alert" : "status"} className={notice.error ? "erro" : "mensagem"}>{notice.text}</p>}
    </section>
  </section>;
}
