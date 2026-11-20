"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useAtlasSession } from "../atlas-frame";

type Profile = { email: string; usuario: string };

export default function Perfil() {
  const { session, refreshSession } = useAtlasSession();
  const [profile, setProfile] = useState<Profile>();
  const [notice, setNotice] = useState("");
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
        if (!controller.signal.aborted) setNotice("Não foi possível ler seu perfil.");
      }
    })();
    return () => controller.abort();
  }, [refreshSession]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    setBusy(true);
    setNotice("");
    try {
      const response = await fetch("/api/perfil", {
        method: "PUT", headers: { "Content-Type": "application/json", "X-CSRF-TOKEN": session.token },
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.erro ?? "Não foi possível atualizar o perfil.");
      }
      form.querySelectorAll<HTMLInputElement>('input[type="password"]').forEach(input => { input.value = ""; });
      await refreshSession();
      setNotice("Perfil atualizado.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Não foi possível atualizar o perfil.");
    } finally { setBusy(false); }
  }

  return <section className="painel login">
    <h1>Meu perfil</h1>
    {profile && <form onSubmit={save}>
      <label>E-mail<input name="email" type="email" defaultValue={profile.email} maxLength={254} required /></label>
      <label>Usuário (opcional)<input name="usuario" defaultValue={profile.usuario} maxLength={60} /></label>
      <label>Senha atual<input name="senhaAtual" type="password" autoComplete="current-password" maxLength={128} required /></label>
      <label>Nova senha (opcional)<input name="novaSenha" type="password" autoComplete="new-password" minLength={12} maxLength={128} /></label>
      <button disabled={busy}>Salvar perfil</button>
    </form>}
    {notice && <p role="status">{notice}</p>}
  </section>;
}
