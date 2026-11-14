"use client";

import { useEffect, useState, type FormEvent } from "react";

type Sessao = { autenticado: boolean; administrador: boolean; token: string };
type Evento = { codigo: string; descricao: string; data: string; protocolo: string; nsu: string; correcao: string };
type Cte = { chave: string; status: string; transportadora: string; data: string };
type Nota = { chave: string; numero: number; serie: number; detalhe: { cancelada: boolean; eventos: Evento[]; ctes: Cte[] } };
type Resultado = { importadoEm: string | null; notas: Nota[]; mais: boolean };

async function lerSessao(): Promise<Sessao> {
  const resposta = await fetch("/api/sessao");
  if (!resposta.ok) throw new Error("A sessão do Atlas está indisponível.");
  return resposta.json();
}

const dataBR = new Intl.DateTimeFormat("pt-BR", {
  dateStyle: "short", timeStyle: "short", timeZone: "America/Sao_Paulo",
});

export default function Consulta() {
  const [sessao, setSessao] = useState<Sessao>();
  const [busca, setBusca] = useState("");
  const [termo, setTermo] = useState("");
  const [pagina, setPagina] = useState(1);
  const [resultado, setResultado] = useState<Resultado>();
  const [erro, setErro] = useState("");
  const [ocupado, setOcupado] = useState(false);

  useEffect(() => {
    lerSessao().then(setSessao).catch(() => setErro("A sessão do Atlas está indisponível."));
  }, []);

  async function autenticar(acao: "login" | "logout", dados?: FormData) {
    if (!sessao) return;
    setOcupado(true);
    setErro("");
    try {
      const resposta = await fetch(`/api/${acao}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-TOKEN": sessao.token },
        body: dados && JSON.stringify({ email: dados.get("email"), senha: dados.get("senha") }),
      });
      if (!resposta.ok) {
        setErro(resposta.status === 429 ? "Aguarde um minuto para tentar de novo." :
          resposta.status === 401 ? "E-mail ou senha inválidos." : "Não foi possível atualizar a sessão.");
        return;
      }
      setResultado(undefined);
      setBusca("");
      setTermo("");
      setSessao(undefined);
      setSessao(await lerSessao());
    } catch {
      setErro("A sessão do Atlas está indisponível.");
    } finally {
      setOcupado(false);
    }
  }

  async function consultar(valor: string, paginaAlvo: number) {
    setOcupado(true);
    setErro("");
    if (paginaAlvo === 1) setResultado(undefined);
    try {
      const parametros = new URLSearchParams({ busca: valor, pagina: String(paginaAlvo) });
      const resposta = await fetch(`/api/consulta?${parametros}`);
      if (resposta.status === 401) {
        setResultado(undefined);
        setSessao(await lerSessao());
        return;
      }
      if (resposta.status === 400) {
        setErro("Informe o número da NF-e ou a chave de acesso de 44 dígitos.");
        return;
      }
      if (!resposta.ok) throw new Error();
      setResultado(await resposta.json());
      setPagina(paginaAlvo);
      setTermo(valor);
    } catch {
      setErro("A Consulta está indisponível. Tente novamente.");
    } finally {
      setOcupado(false);
    }
  }

  function buscar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    void consultar(busca.trim(), 1);
  }

  return <main>
    <header>
      <a href="/" className="marca">Atlas <span>DFE</span></a>
      {sessao?.autenticado && <nav><a href="/emissao">Emissão</a><a href="/aging">Aging</a>{sessao.administrador && <a href="/admin">Administração</a>}<button className="secundario" disabled={ocupado} onClick={() => void autenticar("logout")}>Sair</button></nav>}
    </header>
    {erro && <p role="alert" className="erro">{erro}</p>}
    {!sessao && !erro && <p role="status">Carregando…</p>}
    {sessao?.autenticado === false && <section className="painel login">
      <h1>Entrar no Atlas</h1>
      <form onSubmit={e => { e.preventDefault(); void autenticar("login", new FormData(e.currentTarget)); }}>
        <label>E-mail<input name="email" type="email" autoComplete="username" required /></label>
        <label>Senha<input name="senha" type="password" autoComplete="current-password" required /></label>
        <button disabled={ocupado}>Entrar</button>
      </form>
    </section>}
    {sessao?.autenticado && <section className="painel">
      <h1>Consulta de NF-e</h1>
      <form className="busca" onSubmit={buscar}>
        <label htmlFor="busca">Número da nota ou chave de acesso</label>
        <div>
          <input id="busca" value={busca} inputMode="numeric" maxLength={44} required
            onChange={e => setBusca(e.target.value)} placeholder="Ex. 96203" />
          <button disabled={ocupado}>{ocupado ? "Consultando…" : "Consultar"}</button>
        </div>
      </form>
      {resultado && <div className="resultado">
        {resultado.notas.length > 0 && <a href={`/api/consulta/excel?busca=${encodeURIComponent(termo)}`}>Excel</a>}
        <p role="status">{resultado.importadoEm
          ? `Histórico importado em ${dataBR.format(new Date(resultado.importadoEm))}`
          : "Consulta de documentos recebidos pela SEFAZ."}</p>
        {!resultado.notas.length && <p>Nenhuma NF-e localizada nesta cópia.</p>}
        {resultado.notas.map(nota => <article key={nota.chave} className="nota">
          <h2>NF-e {nota.numero} <small>Série {nota.serie}</small></h2>
          <code>{nota.chave}</code>
          {nota.detalhe.cancelada && <p className="aviso">Cancelada no histórico local</p>}
          <details>
            <summary>Eventos ({nota.detalhe.eventos.length})</summary>
            {nota.detalhe.eventos.length === 0 && <p>Sem eventos registrados.</p>}
            <ul>{nota.detalhe.eventos.map((evento, i) => <li key={i}>
              <strong>{evento.descricao || evento.codigo}</strong> · {evento.data}<br />
              {evento.protocolo && <>Protocolo {evento.protocolo} </>}
              {evento.nsu && <>· NSU {evento.nsu}</>}
              {evento.correcao && <p>{evento.correcao}</p>}
            </li>)}</ul>
          </details>
          <details>
            <summary>CT-e vinculados ({nota.detalhe.ctes.length})</summary>
            {nota.detalhe.ctes.length === 0 && <p>Sem vínculos no histórico local.</p>}
            <ul>{nota.detalhe.ctes.map((cte, i) => <li key={i}>
              <strong>{cte.status}</strong> {cte.transportadora && `· ${cte.transportadora}`}
              {cte.chave && <><br /><code>{cte.chave}</code></>}
            </li>)}</ul>
          </details>
        </article>)}
        {(pagina > 1 || resultado.mais) && <nav aria-label="Páginas de resultados">
          <button className="secundario" disabled={ocupado || pagina === 1}
            onClick={() => void consultar(termo, pagina - 1)}>Anterior</button>
          <span>Página {pagina}</span>
          <button className="secundario" disabled={ocupado || !resultado.mais}
            onClick={() => void consultar(termo, pagina + 1)}>Próxima</button>
        </nav>}
      </div>}
    </section>}
  </main>;
}
