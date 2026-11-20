"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { DownloadsBadge } from "./downloads-badge";
import atlasLight from "./identidade/atlasdfe-light.webp";
import atlasDark from "./identidade/atlasdfe-dark.webp";
import yorozuLight from "./identidade/yorozu-light.webp";
import yorozuDark from "./identidade/yorozu-dark.webp";

type Session = { autenticado: boolean; administrador: boolean; podeExportar?: boolean; cadastroHabilitado?: boolean; token: string };
type SessionContext = { session: Session; refreshSession: () => Promise<void> };
const AtlasSession = createContext<SessionContext | null>(null);
const themeKey = "nfe-theme";

export function useAtlasSession() {
  const context = useContext(AtlasSession);
  if (!context) throw new Error("Sessão do Atlas indisponível.");
  return context;
}

function toggleTheme() {
  const root = document.documentElement;
  const active = root.dataset.theme ?? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = active === "dark" ? "light" : "dark";
  root.dataset.theme = next;
  try { localStorage.setItem(themeKey, next); } catch { /* The theme still applies to this page. */ }
}

const sections = [
  { path: "/", label: "Consulta" },
  { path: "/emissao", label: "Emissão" },
  { path: "/status", label: "Status SEFAZ" },
  { path: "/downloads", label: "Downloads", exportOnly: true },
  { path: "/aging", label: "Aging List", exportOnly: true },
  { path: "/perfil", label: "Perfil" },
  { path: "/admin", label: "Administração", admin: true },
];

const adminSections = [
  { path: "/admin/sefaz", label: "Controles SEFAZ" },
  { path: "/admin", label: "Fila NSU" },
  { path: "/admin/situacao", label: "Situação manual" },
  { path: "/admin/contas", label: "Contas e auditoria" },
  { path: "/admin/importacoes", label: "Importações XML" },
];

export default function AtlasFrame({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [session, setSession] = useState<Session>();
  const [sessionError, setSessionError] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [requested, setRequested] = useState(false);
  const exportPage = pathname === "/downloads" || pathname === "/aging";
  const adminPage = pathname === "/admin" || pathname.startsWith("/admin/");

  const refreshSession = useCallback(async () => {
    try {
      const response = await fetch("/api/sessao", { cache: "no-store" });
      if (!response.ok) throw new Error();
      setSession(await response.json());
      setSessionError(false);
    } catch {
      setSession(undefined);
      setSessionError(true);
    }
  }, []);

  useEffect(() => { void refreshSession(); }, [refreshSession]);

  async function authenticate(action: "login" | "logout", form?: FormData) {
    if (!session || busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`/api/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-TOKEN": session.token },
        body: form && JSON.stringify({ email: form.get("email"), senha: form.get("senha") }),
      });
      if (response.status === 401 && action === "logout") {
        await refreshSession();
        return;
      }
      if (!response.ok) {
        setError(response.status === 429 ? "Aguarde um minuto para tentar de novo." :
          response.status === 401 ? "E-mail ou senha inválidos." : "Não foi possível atualizar a sessão.");
        return;
      }
      if (action === "logout") {
        setSession(undefined);
        router.replace("/");
      }
      await refreshSession();
    } catch {
      setError("A sessão do Atlas está indisponível.");
    } finally {
      setBusy(false);
    }
  }

  function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void authenticate("login", new FormData(event.currentTarget));
  }

  async function register(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session || busy) return;
    setBusy(true);
    setError("");
    try {
      const form = event.currentTarget;
      const response = await fetch("/api/cadastro", {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-TOKEN": session.token },
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.erro ?? "Não foi possível solicitar a conta.");
      }
      form.reset();
      setRequested(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Não foi possível solicitar a conta.");
    } finally { setBusy(false); }
  }

  return <>
    <a href="#conteudo" className="pular">Ir para o conteúdo</a>
    <header className="topbar">
      <Link href="/" className="marca" aria-label="Atlas DFE · Consulta">
        <img className="logo-atlas tema-claro" src={atlasLight.src} alt="Atlas DFE" width={106} height={31} />
        <img className="logo-atlas tema-escuro" src={atlasDark.src} alt="Atlas DFE" width={106} height={31} />
        <span className="marca-divisor">
          <img className="logo-empresa tema-claro" src={yorozuLight.src} alt="Yorozu" width={67} height={18} />
          <img className="logo-empresa tema-escuro" src={yorozuDark.src} alt="Yorozu" width={67} height={18} />
        </span>
      </Link>
      {session?.autenticado && <nav className="abas" aria-label="Navegação principal">
        {sections.filter(section => (!section.admin || session.administrador) && (!section.exportOnly || session.podeExportar !== false)).map(section => {
          const active = section.path === "/admin" ? adminPage : pathname === section.path;
          return <Link key={section.path} href={section.path} aria-current={active ? "page" : undefined}>{section.label}{section.path === "/downloads" && <DownloadsBadge key={session.token} />}</Link>;
        })}
      </nav>}
      <div className="conta">
        <button type="button" className="secundario tema-toggle" onClick={toggleTheme} aria-label="Alternar tema" title="Alternar tema">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
            <circle cx="12" cy="12" r="8" /><path d="M12 4a8 8 0 0 1 0 16Z" fill="currentColor" stroke="none" />
          </svg>
        </button>
        {session?.autenticado && <button type="button" className="secundario" disabled={busy} onClick={() => void authenticate("logout")}>Sair</button>}
      </div>
    </header>
    <main id="conteudo" tabIndex={-1}>
      {error && <p role="alert" className="erro">{error}</p>}
      {sessionError && <section className="painel login">
        <h1>Sessão indisponível</h1>
        <p role="alert">Não foi possível carregar a sessão do Atlas.</p>
        <button onClick={() => void refreshSession()}>Tentar novamente</button>
      </section>}
      {!session && !sessionError && <p className="mensagem" role="status">Carregando sessão…</p>}
      {session?.autenticado === false && <section className="painel login">
        <h1>Entrar no Atlas</h1>
        <form onSubmit={login}>
          <label>E-mail ou usuário<input name="email" type="text" autoComplete="username" maxLength={254} required /></label>
          <label>Senha<input name="senha" type="password" autoComplete="current-password" required /></label>
          <button disabled={busy}>{busy ? "Entrando…" : "Entrar"}</button>
        </form>
        {session.cadastroHabilitado && <details className="resultado">
          <summary>Solicitar conta</summary>
          <form onSubmit={register}>
            <label>E-mail para cadastro<input name="email" type="email" autoComplete="email" maxLength={254} required /></label>
            <label>Usuário (opcional)<input name="usuario" maxLength={60} /></label>
            <label>Senha para cadastro<input name="senha" type="password" minLength={12} maxLength={128} autoComplete="new-password" required /></label>
            <button disabled={busy}>Enviar pedido</button>
          </form>
          {requested && <p role="status">Conta solicitada. Aguarde liberação do administrador.</p>}
        </details>}
      </section>}
      {session?.autenticado && ((adminPage && !session.administrador || exportPage && session.podeExportar === false)
        ? <section className="painel">
            <h1>Acesso restrito</h1>
            <p>Seu perfil não tem acesso a esta área.</p>
            <Link className="acao" href="/">Voltar à Consulta</Link>
          </section>
        : <AtlasSession.Provider value={{ session, refreshSession }}>
            {adminPage && <nav className="abas abas-admin" aria-label="Administração">
              {adminSections.map(section => <Link key={section.path} href={section.path}
                aria-current={pathname === section.path ? "page" : undefined}>{section.label}</Link>)}
            </nav>}
            {children}
          </AtlasSession.Provider>)}
    </main>
  </>;
}
