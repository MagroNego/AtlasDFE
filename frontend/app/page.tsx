"use client";

import { Fragment, useState, type FormEvent } from "react";
import { useAtlasSession } from "./atlas-frame";

type Evento = { codigo: string; descricao: string; data: string; protocolo: string; nsu: string; correcao: string };
type Cte = { chave: string; status: string; transportadora: string; data: string };
type Nota = { chave: string; numero: number; serie: number; detalhe: { cancelada: boolean; eventos: Evento[]; ctes: Cte[] } };
type Resultado = { importadoEm: string | null; notas: Nota[]; mais: boolean };

const dataBR = new Intl.DateTimeFormat("pt-BR", {
  dateStyle: "short", timeStyle: "short", timeZone: "America/Sao_Paulo",
});

export default function Consulta() {
  const { refreshSession } = useAtlasSession();
  const [detalhe, setDetalhe] = useState<string>();
  const [busca, setBusca] = useState("");
  const [termo, setTermo] = useState("");
  const [pagina, setPagina] = useState(1);
  const [resultado, setResultado] = useState<Resultado>();
  const [erro, setErro] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const [copia, setCopia] = useState<{ chave: string; sucesso: boolean }>();

  async function consultar(valor: string, paginaAlvo: number) {
    if (ocupado) return;
    setOcupado(true);
    setDetalhe(undefined);
    setCopia(undefined);
    setErro("");
    if (paginaAlvo === 1) setResultado(undefined);
    try {
      const parametros = new URLSearchParams({ busca: valor, pagina: String(paginaAlvo) });
      const resposta = await fetch(`/api/consulta?${parametros}`);
      if (resposta.status === 401) {
        setResultado(undefined);
        await refreshSession();
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
    const valor = busca.trim();
    const chave = valor.replace(/[\s.-]/g, "");
    void consultar(/^\d{44}$/.test(chave) ? chave : valor, 1);
  }

  async function copiarChave(chave: string) {
    try {
      await navigator.clipboard.writeText(chave);
      setCopia({ chave, sucesso: true });
    } catch {
      setCopia({ chave, sucesso: false });
    }
  }

  return <section className="painel painel-consulta">
    <h1>Consulta de NF-e</h1>
    <form className="busca" onSubmit={buscar}>
      <label htmlFor="busca">Número da nota ou chave de acesso</label>
      <div className="linha-busca">
        <input id="busca" value={busca} inputMode="numeric" autoComplete="off" required
          onChange={e => setBusca(e.target.value)} placeholder="Ex. 96203" />
        <button disabled={ocupado}>{ocupado ? "Consultando…" : "Consultar"}</button>
      </div>
    </form>
    {erro && <p role="alert" className="erro">{erro}</p>}
    {ocupado && <p className="sr-only" role="status">Consultando documentos…</p>}
    {resultado && <div className="resultado" aria-busy={ocupado}>
      <div className="resultado-barra">
        <div className="consulta-resumo">
          {resultado.notas.length > 0 && <p role="status">{resultado.notas.length === 1
            ? "1 nota nesta página" : `${resultado.notas.length} notas nesta página`}</p>}
          {resultado.importadoEm && <p className="muted">Histórico importado em {dataBR.format(new Date(resultado.importadoEm))}</p>}
        </div>
        {resultado.notas.length > 0 && <a className="acao" href={`/api/consulta/excel?busca=${encodeURIComponent(termo)}`}>Exportar Excel</a>}
      </div>
      {!resultado.notas.length ? <p className="mensagem" role="status">Nenhuma NF-e localizada nesta cópia.</p>
        : <div key={pagina} className="tabela-rolagem" tabIndex={0} role="region" aria-label="Resultados da consulta">
          <table className="tabela tabela-consulta" role="table">
            <thead role="rowgroup"><tr role="row"><th scope="col" role="columnheader">NF-e / Série</th><th scope="col" role="columnheader">Chave de acesso</th><th scope="col" role="columnheader">Situação</th><th scope="col" role="columnheader">Eventos e vínculos</th></tr></thead>
            <tbody role="rowgroup">
              {resultado.notas.map(nota => {
                const aberta = detalhe === nota.chave;
                const estadoCopia = copia?.chave === nota.chave ? copia : undefined;
                const { eventos, ctes, cancelada } = nota.detalhe;
                const totalEventos = `${eventos.length} ${eventos.length === 1 ? "evento" : "eventos"}`;
                const temCarta = eventos.some(evento => evento.codigo === "110110");
                return <Fragment key={nota.chave}>
                  <tr role="row" className={aberta ? "consulta-nota aberta" : "consulta-nota"}>
                    <th scope="row" role="rowheader">{nota.numero}<small>Série {nota.serie}</small></th>
                    <td role="cell">
                      <div className="consulta-chave">
                        <code>{nota.chave}</code>
                        <button type="button" className="secundario copiar-chave" title="Copiar chave"
                          aria-label={`Copiar chave da NF-e ${nota.numero}, série ${nota.serie}`}
                          onClick={() => void copiarChave(nota.chave)}>
                          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
                            strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                            {estadoCopia?.sucesso
                              ? <path d="m5 12 4 4L19 6" />
                              : <><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 4H6a2 2 0 0 0-2 2v10" /></>}
                          </svg>
                        </button>
                      </div>
                      {estadoCopia && <p role={estadoCopia.sucesso ? "status" : "alert"} className={estadoCopia.sucesso ? "sr-only" : "consulta-copia-erro"}>
                        {estadoCopia.sucesso ? "Chave de acesso copiada." : "Selecione a chave para copiá-la manualmente."}
                      </p>}
                    </td>
                    <td role="cell">{cancelada ? <span className="selo selo-cancelado">Cancelada no histórico local</span> : <span className="sem-marcacao">—</span>}</td>
                    <td role="cell">
                      <button type="button" className="secundario compacto" aria-expanded={aberta}
                        aria-controls={aberta ? `detalhe-${nota.chave}` : undefined}
                        aria-label={`${totalEventos} e ${ctes.length} CT-e da NF-e ${nota.numero}, série ${nota.serie}${temCarta ? ", com Carta de Correção" : ""}`}
                        onClick={() => setDetalhe(aberta ? undefined : nota.chave)}>
                        {totalEventos} · {ctes.length} CT-e{temCarta && <span className="selo-cce">CC-e</span>}
                      </button>
                    </td>
                  </tr>
                  {aberta && <tr role="row" className="linha-detalhe" id={`detalhe-${nota.chave}`}>
                    <td role="cell" colSpan={4}>
                      <div className="detalhe-grid">
                        <section>
                          <h2>Eventos ({eventos.length})</h2>
                          {eventos.length === 0 && <p>Sem eventos registrados.</p>}
                          {eventos.length > 0 && <ul>{eventos.map((evento, index) => <li key={index}>
                            <strong>{evento.descricao || evento.codigo}</strong> · {evento.data}<br />
                            {evento.protocolo && <>Protocolo {evento.protocolo} </>}
                            {evento.nsu && <>· NSU {evento.nsu}</>}
                            {evento.correcao && <p className="texto-evento consulta-correcao">{evento.correcao}</p>}
                          </li>)}</ul>}
                        </section>
                        <section>
                          <h2>CT-e vinculados ({ctes.length})</h2>
                          {ctes.length === 0 && <p>Sem vínculos no histórico local.</p>}
                          {ctes.length > 0 && <ul>{ctes.map((cte, index) => <li key={index}>
                            <strong>{cte.status}</strong> {cte.transportadora && `· ${cte.transportadora}`}
                            {cte.data && ` · ${cte.data}`}
                            {cte.chave && <><br /><code>{cte.chave}</code></>}
                          </li>)}</ul>}
                        </section>
                      </div>
                    </td>
                  </tr>}
                </Fragment>;
              })}
            </tbody>
          </table>
        </div>}
      {(pagina > 1 || resultado.mais) && <nav className="paginacao" aria-label="Páginas de resultados">
        <button className="secundario" disabled={ocupado || pagina === 1}
          onClick={() => void consultar(termo, pagina - 1)}>Anterior</button>
        <span>Página {pagina}</span>
        <button className="secundario" disabled={ocupado || !resultado.mais}
          onClick={() => void consultar(termo, pagina + 1)}>Próxima</button>
      </nav>}
    </div>}
  </section>;
}
