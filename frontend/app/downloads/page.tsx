"use client";

import { useEffect, useState } from "react";
import { useAtlasSession } from "../atlas-frame";

type Estado = "na_fila" | "consultando" | "gerando" | "salvando" | "pronto" | "falhou" | "cancelado";
type Download = {
  id: string;
  tipo: string;
  nome: string | null;
  estado: Estado;
  criadoEm: string;
  concluidoEm: string | null;
  erro: string | null;
  visto: boolean;
};

const estado: Record<Estado, string> = {
  na_fila: "Na fila", consultando: "Consultando dados", gerando: "Gerando arquivo",
  salvando: "Salvando arquivo", pronto: "Pronto", falhou: "Falhou", cancelado: "Cancelado",
};
const tipo: Record<string, string> = {
  consulta: "Consulta de manifestações", lote: "Consulta por TXT", emissao: "Emissão", itens: "Itens da NF-e",
};
const format = new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short", timeZone: "America/Sao_Paulo" });

export default function Downloads() {
  const { session, refreshSession } = useAtlasSession();
  const [entries, setEntries] = useState<Download[]>();
  const [error, setError] = useState("");
  const [cancelError, setCancelError] = useState("");
  const [canceling, setCanceling] = useState("");
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const response = await fetch("/api/downloads", { cache: "no-store", signal: controller.signal });
        if (response.status === 401) { await refreshSession(); return; }
        if (!response.ok) throw new Error();
        const results: Download[] = await response.json();
        if (controller.signal.aborted) return;
        setEntries(results);
        setError("");
        const visible = results.filter(entry => entry.concluidoEm && !entry.visto && entry.estado !== "cancelado");
        if (visible.length) {
          const acknowledge = await fetch("/api/downloads/vistas", {
            method: "POST", signal: controller.signal,
            headers: { "Content-Type": "application/json", "X-CSRF-TOKEN": session.token },
            body: JSON.stringify(visible.map(entry => entry.id)),
          });
          if (acknowledge.ok) window.dispatchEvent(new Event("atlas-downloads-vistos"));
        }
      } catch {
        if (!controller.signal.aborted) setError("Não foi possível atualizar os downloads.");
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(load, 2500);
      }
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [refresh, refreshSession, session.token]);

  async function cancel(id: string) {
    setCanceling(id);
    setCancelError("");
    try {
      const response = await fetch(`/api/downloads/${id}/cancelar`, {
        method: "POST", headers: { "X-CSRF-TOKEN": session.token },
      });
      if (response.status === 401) { await refreshSession(); return; }
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        setCancelError(data.erro ?? "Não foi possível cancelar o pedido.");
      }
    } catch { setCancelError("Não foi possível cancelar o pedido."); }
    finally {
      setCanceling("");
      setRefresh(previous => previous + 1);
    }
  }

  return <section className="tela-fiscal">
    <div className="titulo-fiscal"><h1>Downloads</h1></div>
    <section className="painel-fiscal">
      <p className="muted">Até 20 pedidos concluídos ficam disponíveis por até 7 dias.</p>
      {error && <p role="alert" className="erro">{error}</p>}
      {cancelError && <p role="alert" className="erro">{cancelError}</p>}
      {!entries && !error && <p role="status">Carregando downloads…</p>}
      {entries?.length === 0 && <p>Nenhum pedido de download.</p>}
      {entries?.map(entry => <article className="nota download-item" key={entry.id}>
        <div>
          <strong>{tipo[entry.tipo]}</strong>
          {entry.nome && <p className="download-nome">{entry.nome}</p>}
          <small>{format.format(new Date(entry.criadoEm))}</small>
        </div>
        <div role="status">
          {estado[entry.estado]}
          {entry.erro && <small className="download-nome">{entry.erro}</small>}
        </div>
        {entry.estado === "pronto" && <a className="acao" href={`/api/downloads/${entry.id}/arquivo`}>Baixar arquivo</a>}
        {!entry.concluidoEm && <button type="button" className="secundario" disabled={!!canceling}
          onClick={() => void cancel(entry.id)}>{canceling === entry.id ? "Cancelando…" : "Cancelar"}</button>}
      </article>)}
    </section>
  </section>;
}
