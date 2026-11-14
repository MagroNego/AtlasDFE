"use client";
import { useEffect, useState, type FormEvent } from "react";

type Account = { email: string; admin: boolean; ativa: boolean };
type Audit = { usuario: string; acao: string; alvo: string; criadoEm: string };

export default function Accounts() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [audit, setAudit] = useState<Audit[]>([]);
  const [action, setAction] = useState("criar");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function refresh() {
    const [users, entries] = await Promise.all([fetch("/api/admin/contas"), fetch("/api/admin/auditoria")]);
    if (users.status === 401 || users.status === 403) { location.href = "/"; return; }
    if (!users.ok || !entries.ok) throw new Error("Não foi possível carregar contas e auditoria.");
    setAccounts(await users.json());
    setAudit(await entries.json());
  }

  useEffect(() => { void refresh().catch(() => setMessage("Não foi possível carregar contas e auditoria.")); }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    setBusy(true);
    setMessage("");
    try {
      const session = await fetch("/api/sessao");
      if (!session.ok) throw new Error("Sessão indisponível.");
      const { token } = await session.json();
      const response = await fetch("/api/admin/contas", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-TOKEN": token },
        body: JSON.stringify({ email: fields.get("email"), acao: action, senha: fields.get("senha"), admin: fields.has("admin") }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.erro ?? "Não foi possível atualizar a conta.");
      }
      form.reset();
      await refresh();
      setMessage("Conta atualizada.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Não foi possível atualizar a conta.");
    } finally { setBusy(false); }
  }

  return <main>
    <header><a className="marca" href="/">Atlas <span>DFE</span></a><a href="/admin">Administração</a></header>
    <section className="painel">
      <h1>Contas</h1>
      <form onSubmit={save}>
        <label>E-mail<input name="email" type="email" maxLength={254} required /></label>
        <label>Ação <select value={action} onChange={e => setAction(e.target.value)}>
          <option value="criar">Criar conta</option><option value="senha">Trocar senha</option>
          <option value="bloquear">Bloquear acesso</option><option value="liberar">Liberar acesso</option>
        </select></label>
        {(action === "criar" || action === "senha") &&
          <label>Senha temporária<input name="senha" type="password" minLength={12} maxLength={128} autoComplete="new-password" required /></label>}
        {action === "criar" && <label className="inline-check"><input type="checkbox" name="admin" /> Administrador</label>}
        <button disabled={busy}>Salvar</button>
      </form>
      {message && <p role="status">{message}</p>}
      {accounts.map(account => <p className="nota" key={account.email}>{account.email} · {account.admin ? "Administrador" : "Consulta"} · {account.ativa ? "Ativa" : "Bloqueada"}</p>)}
      <h2>Auditoria</h2>
      {audit.map((entry, index) => <p className="nota" key={index}>{new Date(entry.criadoEm).toLocaleString("pt-BR")} · {entry.usuario} · {entry.acao} · {entry.alvo}</p>)}
    </section>
  </main>;
}
