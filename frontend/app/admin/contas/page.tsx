"use client";
import { useEffect, useState, type FormEvent } from "react";
import { useAtlasSession } from "../../atlas-frame";

type Account = { email: string; admin: boolean; userName: string; perfil: string; ativa: boolean };
type Audit = { usuario: string; acao: string; alvo: string; criadoEm: string };

export default function Accounts() {
  const { session, refreshSession } = useAtlasSession();
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
    if (busy) return;
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch("/api/admin/contas", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-TOKEN": session.token },
        body: JSON.stringify({ email: fields.get("email"), acao: action, senha: fields.get("senha"), perfil: fields.get("perfil"), novoEmail: fields.get("novoEmail"), usuario: fields.get("usuario") }),
      });
      if (response.status === 401 || response.status === 403) {
        await refreshSession();
        throw new Error("Seu acesso precisa ser revalidado.");
      }
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

  return <section className="painel">
    <h1>Contas</h1>
    <form onSubmit={save}>
      <label>E-mail<input name="email" type="email" maxLength={254} required /></label>
      <label>Ação <select value={action} onChange={e => setAction(e.target.value)}>
        <option value="criar">Criar conta</option><option value="senha">Trocar senha</option>
        <option value="editar">Editar conta</option><option value="excluir">Excluir conta</option>
        <option value="bloquear">Bloquear acesso</option><option value="liberar">Liberar acesso</option>
      </select></label>
      {(action === "criar" || action === "senha") &&
        <label>Senha temporária<input name="senha" type="password" minLength={12} maxLength={128} autoComplete="new-password" required /></label>}
      {action === "editar" && <label>Novo e-mail<input name="novoEmail" type="email" maxLength={254} required /></label>}
      {(action === "criar" || action === "editar") && <>
        <label>Usuário (opcional)<input name="usuario" maxLength={60} autoComplete="off" /></label>
        <label>Perfil<select name="perfil" defaultValue="fiscal">
          <option value="consulta">Consulta</option><option value="fiscal">Fiscal</option>
          <option value="administrador">Administrador</option>
        </select></label>
      </>}
      {action === "excluir" && <label className="inline-check"><input type="checkbox" required /> Confirmo a exclusão desta conta</label>}
      <button disabled={busy}>Salvar</button>
    </form>
    {message && <p role="status">{message}</p>}
    {accounts.map(account => <p className="nota" key={account.email}>{account.email} · {account.perfil === "administrador" ? "Administrador" : account.perfil === "fiscal" ? "Fiscal" : "Consulta"}
      {account.userName !== account.email && <> · {account.userName}</>} · {account.ativa ? "Ativa" : "Bloqueada"}</p>)}
    <h2>Auditoria</h2>
    {audit.map((entry, index) => <p className="nota" key={index}>{new Date(entry.criadoEm).toLocaleString("pt-BR")} · {entry.usuario} · {entry.acao} · {entry.alvo}</p>)}
  </section>;
}
