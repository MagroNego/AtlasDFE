"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useAtlasSession } from "../../atlas-frame";

type Settings = {
  agenda: { habilitada: boolean; inicio: string; fim: string; horarios: string; dias: number[] };
  alertas: { habilitados: boolean; destinatarios: string };
  servidorSmtp: boolean;
  certificadoDisponivel: boolean;
  certificado: { impressaoDigital: string; validoDesde: string; validoAte: string } | null;
};

const api = "/api/admin/sefaz";
const weekdays = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"];
const dayFormat = new Intl.DateTimeFormat("pt-BR", { timeZone: "America/Sao_Paulo", dateStyle: "short" });

export default function SefazControls() {
  const { session, refreshSession } = useAtlasSession();
  const [settings, setSettings] = useState<Settings>();
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState<{ error: boolean; text: string }>();
  const [confirmation, setConfirmation] = useState("");

  const load = useCallback(async (signal?: AbortSignal) => {
    const response = await fetch(api, { signal, cache: "no-store" });
    if (response.status === 401 || response.status === 403) await refreshSession();
    if (!response.ok) throw new Error("Não foi possível ler as configurações SEFAZ.");
    const data: Settings = await response.json();
    if (!signal?.aborted) setSettings(data);
  }, [refreshSession]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal).catch(error => {
      if (!controller.signal.aborted) setNotice({ error: true, text: error.message });
    });
    return () => controller.abort();
  }, [load]);

  async function action(label: string, path: string, method: string, body?: BodyInit,
    onSuccess?: (response: Response) => Promise<string>) {
    if (busy) return;
    setBusy(label);
    setNotice(undefined);
    try {
      const headers: Record<string, string> = { "X-CSRF-TOKEN": session.token };
      if (typeof body === "string") headers["Content-Type"] = "application/json";
      const response = await fetch(api + path, { method, headers, body });
      if (response.status === 401 || response.status === 403) await refreshSession();
      if (!response.ok) {
        const problem = await response.json().catch(() => ({}));
        throw new Error(problem.erro ?? (response.status === 429 ? "Aguarde um minuto antes de tentar novamente." :
          "Não foi possível concluir a operação."));
      }
      const text = onSuccess ? await onSuccess(response) : "Alteração salva.";
      setNotice({ error: false, text });
    } catch (error) {
      setNotice({ error: true, text: error instanceof Error ? error.message : "Falha na operação." });
    } finally { setBusy(""); }
  }

  function saveAgenda(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const data = { habilitada: form.has("habilitada"), inicio: form.get("inicio"), fim: form.get("fim"),
      horarios: form.get("horarios"), dias: form.getAll("dias").map(Number) };
    void action("agenda", "/agenda", "PUT", JSON.stringify(data));
  }

  function saveAlerts(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    void action("alertas", "/alertas", "PUT", JSON.stringify({
      habilitados: form.has("habilitados"), destinatarios: form.get("destinatarios"),
    }), async () => {
      await load();
      return "Alertas salvos.";
    });
  }

  function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!window.isSecureContext) {
      setNotice({ error: true, text: "Abra o Atlas por HTTPS ou localhost para enviar o certificado." });
      return;
    }
    void action("certificado", "/certificado", "POST", new FormData(form), async () => {
      form.reset();
      await load();
      return "Certificado salvo.";
    });
  }

  function synchronize() {
    setConfirmation("");
    void action("sync", "/sincronizar", "POST", undefined, async response => {
      const { cstat, cursor, documentos } = await response.json();
      return `Retorno ${cstat} · NSU ${String(cursor).padStart(15, "0")} · ${documentos} documento(s).`;
    });
  }

  const disabled = busy !== "";
  return <section className="tela-fiscal">
    <div className="titulo-fiscal"><h1>Controles SEFAZ</h1><Link href="/status" className="acao">Ver Status SEFAZ</Link></div>
    {notice && <p role={notice.error ? "alert" : "status"} className={notice.error ? "erro" : "mensagem"}>{notice.text}</p>}
    {busy && <p role="status" className="mensagem">Operação em andamento…</p>}
    {!settings && !notice && <p role="status">Carregando configurações…</p>}
    {settings && <>
      <section className="painel-fiscal">
        <h2>Sincronização manual</h2>
        {confirmation === "sync" ? <div className="form-acoes">
          <p>Iniciar uma consulta de distribuição com o cursor salvo?</p>
          <button onClick={synchronize} disabled={disabled}>Confirmar sincronização</button>
          <button className="secundario" onClick={() => setConfirmation("")}>Cancelar</button>
        </div> : <button onClick={() => setConfirmation("sync")} disabled={disabled}>Sincronizar agora</button>}
      </section>

      <section className="painel-fiscal resultado">
        <h2>Agenda automática</h2>
        <form onSubmit={saveAgenda}>
          <label className="inline-check"><input type="checkbox" name="habilitada" defaultChecked={settings.agenda.habilitada} /> Ativar agenda</label>
          <div className="campos-fiscais campos-lote">
            <label>Início da janela<input type="time" name="inicio" defaultValue={settings.agenda.inicio} required /></label>
            <label>Fim da janela<input type="time" name="fim" defaultValue={settings.agenda.fim} required /></label>
          </div>
          <label>Horários (Brasília)<input name="horarios" defaultValue={settings.agenda.horarios} maxLength={200}
            placeholder="22:00,23:00,00:00" required /></label>
          <fieldset><legend>Dias da semana</legend><div className="form-acoes">
            {weekdays.map((day, number) => <label key={day} className="inline-check">
              <input type="checkbox" name="dias" value={number} defaultChecked={settings.agenda.dias.includes(number)} /> {day}
            </label>)}
          </div></fieldset>
          <button disabled={disabled}>Salvar agenda</button>
        </form>
      </section>

      <section className="painel-fiscal resultado">
        <h2>Alertas por e-mail</h2>
        {!settings.servidorSmtp && <p className="muted">Configure o servidor SMTP na instalação para enviar alertas.</p>}
        <form onSubmit={saveAlerts}>
          <label className="inline-check"><input type="checkbox" name="habilitados" defaultChecked={settings.alertas.habilitados} /> Ativar alertas</label>
          <label>Destinatários<textarea name="destinatarios" defaultValue={settings.alertas.destinatarios.replaceAll(",", "\n")}
            rows={3} maxLength={8000} placeholder="Um e-mail por linha" /></label>
          <button disabled={disabled}>Salvar alertas</button>
        </form>
        {settings.alertas.destinatarios && <form className="resultado" onSubmit={event => {
          event.preventDefault();
          const email = new FormData(event.currentTarget).get("email");
          void action("teste", "/alertas/teste", "POST", JSON.stringify({ email }), async () => "E-mail de teste enviado.");
        }}>
          <label>Destinatário do teste<select name="email" required>
            {settings.alertas.destinatarios.split(",").filter(Boolean).map(email => <option key={email}>{email}</option>)}
          </select></label>
          <div className="form-acoes">
            <button className="secundario" disabled={disabled || !settings.servidorSmtp}>Enviar teste</button>
            <button type="button" className="secundario" disabled={disabled || !settings.alertas.habilitados}
              onClick={() => void action("reagendar", "/alertas/reagendar", "POST", undefined, async response => {
                const { reagendados } = await response.json();
                return `${reagendados} entrega(s) reagendada(s).`;
              })}>Reagendar pendentes</button>
          </div>
        </form>}
      </section>

      <section className="painel-fiscal resultado">
        <h2>Certificado A1</h2>
        {settings.certificado ? <p>Válido até {dayFormat.format(new Date(settings.certificado.validoAte))} ·
          {" "}<code>{settings.certificado.impressaoDigital}</code></p> :
          <p className="muted">{settings.certificadoDisponivel ? "Usando certificado configurado no serviço." : "Nenhum certificado configurado."}</p>}
        <form onSubmit={upload}>
          <label>Arquivo PFX ou P12<input name="arquivo" type="file" accept=".pfx,.p12" required /></label>
          <label>Senha do certificado<input name="senha" type="password" autoComplete="new-password" maxLength={256} /></label>
          <button disabled={disabled}>Salvar certificado</button>
        </form>
        {settings.certificado && <div className="resultado form-acoes">
          {confirmation === "certificado" ? <>
            <p>Remover o certificado salvo e voltar à configuração do serviço?</p>
            <button type="button" className="secundario" disabled={disabled} onClick={() => {
              setConfirmation("");
              void action("remover", "/certificado", "DELETE", undefined, async () => {
                await load();
                return "Certificado removido.";
              });
            }}>Confirmar remoção</button>
            <button type="button" className="secundario" onClick={() => setConfirmation("")}>Cancelar</button>
          </> : <button type="button" className="secundario" disabled={disabled}
            onClick={() => setConfirmation("certificado")}>Remover certificado salvo</button>}
        </div>}
      </section>
    </>}
  </section>;
}
