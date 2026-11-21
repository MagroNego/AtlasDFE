"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Janela } from "../../janela";
import { useAtlasSession } from "../../atlas-frame";

type Account = { id: string; email: string; userName: string; perfil: string; ativa: boolean; minhaConta: boolean };
type Audit = { usuario: string; acao: string; alvo: string; criadoEm: string };
type Action = "criar" | "editar" | "senha" | "bloquear" | "liberar" | "excluir";

const roles: Record<string, string> = { administrador: "Administrador", fiscal: "Fiscal", consulta: "Consulta" };
const actions: Record<Action, string> = {
  criar: "Criar conta", editar: "Editar dados e perfil", senha: "Trocar senha",
  bloquear: "Inativar conta", liberar: "Ativar conta", excluir: "Excluir conta",
};
const auditLabels: Record<string, string> = {
  "conta.criar": "Criação de conta", "conta.editar": "Alteração de dados e perfil", "conta.senha": "Troca de senha",
  "conta.bloquear": "Conta inativada", "conta.liberar": "Conta ativada", "conta.excluir": "Exclusão de conta",
  "conta.perfil": "Alteração da própria conta", "conta.solicitar": "Solicitação de conta",
};
const auditTime = new Intl.DateTimeFormat("pt-BR", {
  timeZone: "America/Sao_Paulo", dateStyle: "short", timeStyle: "medium",
});

export default function Accounts() {
  const { session, refreshSession } = useAtlasSession();
  const [accounts, setAccounts] = useState<Account[]>();
  const [audit, setAudit] = useState<Audit[]>([]);
  const [auditError, setAuditError] = useState(false);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Account>();
  const [stage, setStage] = useState<Action>();
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [dialogError, setDialogError] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    setError("");
    const [users, entries] = await Promise.all([fetch("/api/admin/contas", { signal }), fetch("/api/admin/auditoria", { signal })]);
    if (users.status === 401 || users.status === 403) { await refreshSession(); return; }
    if (!users.ok) throw new Error("Não foi possível carregar a lista de contas.");
    setAccounts(await users.json());
    setAuditError(!entries.ok);
    if (entries.ok) setAudit(await entries.json());
  }, [refreshSession]);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal).catch(() => {
      if (!controller.signal.aborted) setError("Não foi possível carregar a lista de contas.");
    });
    return () => controller.abort();
  }, [refresh]);

  function open(account?: Account) {
    setSelected(account);
    setStage(account ? undefined : "criar");
    setDialogError("");
  }

  function close() {
    setSelected(undefined);
    setStage(undefined);
    setDialogError("");
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !stage) return;
    const fields = new FormData(event.currentTarget);
    setBusy(true);
    setDialogError("");
    try {
      const response = await fetch("/api/admin/contas", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-TOKEN": session.token },
        body: JSON.stringify({ email: selected?.email ?? fields.get("email"), contaId: selected?.id, acao: stage,
          senha: fields.get("senha"), perfil: fields.get("perfil"), novoEmail: fields.get("novoEmail"), usuario: fields.get("usuario") }),
      });
      if (response.status === 401 || response.status === 403) {
        await refreshSession();
        throw new Error("Seu acesso precisa ser revalidado.");
      }
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        if (response.status === 409) await refresh().catch(() => {});
        throw new Error(data.erro ?? "Não foi possível atualizar a conta.");
      }
      close();
      setMessage(stage === "criar" ? "Conta criada." : stage === "excluir" ? "Conta excluída." : "Conta atualizada.");
      await refresh().catch(() => setError("A alteração foi salva. Atualize a lista para ver o resultado."));
    } catch (reason) {
      setDialogError(reason instanceof Error ? reason.message : "Não foi possível atualizar a conta.");
    } finally { setBusy(false); }
  }

  const query = search.trim().toLocaleLowerCase("pt-BR");
  const matches = accounts?.filter(account =>
    [account.email, account.userName, roles[account.perfil]].some(value => value?.toLocaleLowerCase("pt-BR").includes(query)));

  return <section className="tela-fiscal">
    <div className="titulo-fiscal">
      <h1>Contas</h1>
      <button type="button" aria-haspopup="dialog" onClick={() => open()} disabled={busy}>Nova conta</button>
    </div>
    {message && <p role="status" className="mensagem">{message}</p>}
    {error && <p role="alert" className="erro">{error} <button type="button" className="secundario"
      onClick={() => void refresh().catch(() => setError("Não foi possível carregar a lista de contas."))}>Atualizar lista</button></p>}
    <section className="painel-fiscal contas-painel" aria-label="Contas cadastradas">
      <label>Buscar conta<input type="search" placeholder="E-mail, usuário ou perfil" value={search}
        onChange={event => setSearch(event.currentTarget.value)} /></label>
      {!accounts ? !error && <p className="muted" role="status">Carregando contas…</p>
        : !matches?.length ? <p className="muted">Nenhuma conta encontrada.</p>
        : <ul className="contas-lista">{matches.map(account => <li key={account.id} className="conta-linha">
          <span className="conta-avatar" aria-hidden="true">{(account.userName || account.email).slice(0, 1).toUpperCase()}</span>
          <div className="conta-dados">
            <strong>{account.email}</strong>
            <small>{[account.userName !== account.email && account.userName, roles[account.perfil], account.minhaConta && "Você"].filter(Boolean).join(" · ")}</small>
          </div>
          <span className="conta-estado" data-ativa={account.ativa}>{account.ativa ? "Ativa" : "Inativa"}</span>
          {!account.minhaConta && <button type="button" className="secundario controle-icone" aria-haspopup="dialog"
            aria-label={`Gerenciar ${account.email}`} title="Gerenciar conta" disabled={busy} onClick={() => open(account)}>
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
              <path d="M5 6h14M5 12h14M5 18h14" /><path d="M8 4v4m8 2v4m-5 2v4" strokeWidth="2.7" />
            </svg>
          </button>}
        </li>)}</ul>}
    </section>
    <section className="painel-fiscal">
      <h2>Auditoria</h2>
      {auditError && <p role="alert" className="erro">Não foi possível carregar a auditoria.</p>}
      {accounts && !auditError && audit.length === 0 && <p className="muted">Nenhuma alteração registrada.</p>}
      <ul className="contas-lista">{audit.map((entry, index) => <li className="auditoria-linha" key={index}>
        <div><strong>{auditLabels[entry.acao] ?? entry.acao}</strong>{entry.alvo && <span> · {entry.alvo}</span>}</div>
        <small>{auditTime.format(new Date(entry.criadoEm))} · {entry.usuario}</small>
      </li>)}</ul>
    </section>
    {(selected || stage === "criar") && <Janela titulo={stage ? actions[stage] : "Gerenciar conta"} busy={busy} onClose={close}>
      {selected && <p className="conta-alvo muted">{selected.email}</p>}
      {!stage && selected && <div className="conta-opcoes">
        {(["editar", "senha", selected.ativa ? "bloquear" : "liberar", "excluir"] as const).map(action =>
          <button key={action} type="button" className={`secundario ${action === "excluir" ? "acao-perigo" : ""}`}
            onClick={() => { setDialogError(""); setStage(action); }}>{actions[action]}</button>)}
      </div>}
      {stage && <form key={stage} onSubmit={save}>
        {stage === "criar" && <label>E-mail<input name="email" type="email" maxLength={254} required /></label>}
        {stage === "editar" && <label>E-mail<input name="novoEmail" type="email" maxLength={254} defaultValue={selected?.email} required /></label>}
        {(stage === "criar" || stage === "editar") && <>
          <label>Usuário (opcional)<input name="usuario" maxLength={60} autoComplete="off"
            defaultValue={selected?.userName === selected?.email ? "" : selected?.userName} /></label>
          <label>Perfil<select name="perfil" defaultValue={selected?.perfil ?? "fiscal"}>
            {Object.entries(roles).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select></label>
        </>}
        {(stage === "criar" || stage === "senha") &&
          <label>{stage === "criar" ? "Senha inicial" : "Nova senha"}<input name="senha" type="password"
            minLength={12} maxLength={128} autoComplete="new-password" required /></label>}
        {stage === "excluir" && <label className="inline-check"><input type="checkbox" required /> Confirmo a exclusão desta conta.</label>}
        {stage === "bloquear" && <p>O usuário perderá o acesso até que a conta seja ativada novamente.</p>}
        {stage === "liberar" && <p>O usuário poderá acessar o Atlas novamente.</p>}
        {dialogError && <p role="alert" className="erro">{dialogError}</p>}
        <div className="form-acoes">
          <button className={stage === "excluir" ? "acao-perigo secundario" : undefined} disabled={busy}>
            {busy ? "Salvando…" : stage === "editar" ? "Salvar alterações" : actions[stage]}
          </button>
          {selected && <button type="button" className="secundario" disabled={busy}
            onClick={() => { setDialogError(""); setStage(undefined); }}>Voltar</button>}
        </div>
      </form>}
    </Janela>}
  </section>;
}
