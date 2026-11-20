"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Janela } from "./janela";

type Profile = { email: string; usuario: string };

export function MinhaConta({ token, refreshSession, onClose }: {
  token: string; refreshSession: () => Promise<void>; onClose: () => void;
}) {
  const [profile, setProfile] = useState<Profile>();
  const [notice, setNotice] = useState("");
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch("/api/perfil", { signal: controller.signal });
        if (response.status === 401) { await refreshSession(); return; }
        if (!response.ok) throw new Error();
        setProfile(await response.json());
      } catch {
        if (!controller.signal.aborted) {
          setFailed(true);
          setNotice("Não foi possível carregar sua conta.");
        }
      }
    })();
    return () => controller.abort();
  }, [refreshSession]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    setBusy(true);
    setFailed(false);
    setNotice("");
    try {
      const response = await fetch("/api/perfil", {
        method: "PUT", headers: { "Content-Type": "application/json", "X-CSRF-TOKEN": token },
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      });
      if (response.status === 401) { await refreshSession(); return; }
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.erro ?? "Não foi possível atualizar o perfil.");
      }
      form.querySelectorAll<HTMLInputElement>('input[type="password"]').forEach(input => { input.value = ""; });
      await refreshSession();
      setNotice("Conta atualizada.");
    } catch (error) {
      setFailed(true);
      setNotice(error instanceof Error ? error.message : "Não foi possível atualizar o perfil.");
    } finally { setBusy(false); }
  }

  return <Janela titulo="Minha conta" busy={busy} onClose={onClose}>
    {!profile && !notice && <p role="status" className="muted">Carregando sua conta…</p>}
    {profile && <form onSubmit={save}>
      <label>E-mail<input name="email" type="email" defaultValue={profile.email} maxLength={254} required /></label>
      <label>Usuário (opcional)<input name="usuario" defaultValue={profile.usuario} maxLength={60} /></label>
      <label>Senha atual<input name="senhaAtual" type="password" autoComplete="current-password" maxLength={128} required /></label>
      <label>Nova senha (opcional)<input name="novaSenha" type="password" autoComplete="new-password" minLength={12} maxLength={128} /></label>
      <button disabled={busy}>{busy ? "Salvando…" : "Salvar alterações"}</button>
    </form>}
    {notice && <p role={failed ? "alert" : "status"} className={failed ? "erro" : "mensagem"}>{notice}</p>}
  </Janela>;
}
