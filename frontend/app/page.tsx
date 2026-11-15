"use client";

import { Fragment, useState, type FormEvent } from "react";
import { useAtlasSession } from "./atlas-frame";
import { EventosNota, rotulosAcoes, type AcaoNota, type ResultadoConsulta } from "./eventos-nota";
import { ItensNota, type ItemNota } from "./itens-nota";

export default function Consulta() {
  const { refreshSession } = useAtlasSession();
  const [detalhe, setDetalhe] = useState<{ chave: string; tipo: AcaoNota; itens?: ItemNota[] }>();
  const [termo, setTermo] = useState("");
  const [pagina, setPagina] = useState(1);
  const [resultado, setResultado] = useState<ResultadoConsulta>();
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
    const campos = new FormData(evento.currentTarget);
    const numero = String(campos.get("numero") ?? "").trim();
    const chaveInformada = String(campos.get("chave") ?? "").trim();
    if ((!numero && !chaveInformada) || (numero && chaveInformada)) {
      setErro("Informe apenas o número da nota ou a chave de acesso.");
      return;
    }
    const valor = numero || chaveInformada;
    const chave = valor.replace(/[\s.-]/g, "");
    void consultar(/^\d{44}$/.test(chave) ? chave : valor, 1);
  }

  function limpar() {
    setResultado(undefined);
    setDetalhe(undefined);
    setCopia(undefined);
    setErro("");
  }

  async function abrirDetalhe(chave: string, tipo: AcaoNota) {
    if (ocupado) return;
    if (detalhe?.chave === chave && detalhe.tipo === tipo) {
      setDetalhe(undefined);
      return;
    }
    setErro("");
    if (tipo !== "itens") {
      setDetalhe({ chave, tipo });
      return;
    }
    setOcupado(true);
    try {
      const resposta = await fetch(`/api/emissao/${encodeURIComponent(chave)}/itens`);
      if (resposta.status === 401) {
        setResultado(undefined);
        await refreshSession();
        return;
      }
      if (!resposta.ok) throw new Error();
      setDetalhe({ chave, tipo, itens: await resposta.json() });
    } catch {
      setErro("Não foi possível abrir os itens.");
    } finally {
      setOcupado(false);
    }
  }

  async function copiarChave(chave: string) {
    try {
      await navigator.clipboard.writeText(chave);
      setCopia({ chave, sucesso: true });
    } catch {
      setCopia({ chave, sucesso: false });
    }
  }

  return <section className="tela-fiscal tela-consulta">
    <div className="titulo-fiscal"><h1>Consulta</h1></div>
    <form className="filtros-fiscais painel-fiscal" onSubmit={buscar} onReset={limpar}>
      <h2>Filtros</h2>
      <div className="campos-fiscais campos-consulta">
        <label>Número da NF
          <input name="numero" inputMode="numeric" autoComplete="off" placeholder="Ex.: 96203" />
        </label>
        <label>Chave NF-e
          <input name="chave" inputMode="numeric" autoComplete="off" placeholder="44 dígitos" />
        </label>
      </div>
      <div className="acoes-filtros">
        <button type="reset" className="secundario" disabled={ocupado}>Limpar</button>
        <button disabled={ocupado}>{ocupado ? "Consultando…" : "Consultar"}</button>
      </div>
    </form>
    {erro && <p role="alert" className="erro">{erro}</p>}
    {ocupado && <p className="sr-only" role="status">Consultando documentos…</p>}
    {resultado && <div className="resultado painel-fiscal" aria-busy={ocupado}>
      {resultado.notas.length > 0 && <div className="resultado-barra">
        <a className="acao" href={`/api/consulta/excel?busca=${encodeURIComponent(termo)}`}>Exportar Excel</a>
      </div>}
      {!resultado.notas.length ? <p className="mensagem" role="status">Nenhuma NF-e localizada nesta cópia.</p>
        : <div key={pagina} className="tabela-rolagem" tabIndex={0} role="region" aria-label="Resultados da consulta">
          <table className="tabela tabela-consulta" role="table">
            <thead role="rowgroup"><tr role="row"><th scope="col" role="columnheader">Ações</th><th scope="col" role="columnheader">NF-e</th><th scope="col" role="columnheader">Chave de acesso</th><th scope="col" role="columnheader">Situação</th></tr></thead>
            <tbody role="rowgroup">
              {resultado.notas.map(nota => {
                const detalheAberto = detalhe?.chave === nota.chave ? detalhe : undefined;
                const estadoCopia = copia?.chave === nota.chave ? copia : undefined;
                const { eventos, ctes, cancelada } = nota.detalhe;
                const totalEventos = `${eventos.length} ${eventos.length === 1 ? "evento" : "eventos"}`;
                const temCarta = eventos.some(evento => evento.codigo === "110110");
                const acoes: AcaoNota[] = ["eventos"];
                if (nota.temItens) acoes.unshift("itens");
                if (temCarta) acoes.push("cce");
                if (ctes.length > 0) acoes.push("cte");
                return <Fragment key={nota.chave}>
                  <tr role="row" className={detalheAberto ? "consulta-nota aberta" : "consulta-nota"}>
                    <td role="cell">
                      <div className="acoes-nota">
                        {acoes.map(tipo => <button key={tipo} type="button" className="secundario compacto acao-nota"
                          disabled={ocupado} aria-expanded={detalheAberto?.tipo === tipo}
                          aria-controls={detalheAberto?.tipo === tipo ? `detalhe-${nota.chave}` : undefined}
                          aria-label={`${rotulosAcoes[tipo]} da NF-e ${nota.numero}, série ${nota.serie}${tipo === "eventos" ? ` · ${totalEventos}` : ""}`}
                          onClick={() => void abrirDetalhe(nota.chave, tipo)}>{rotulosAcoes[tipo]}</button>)}
                      </div>
                    </td>
                    <th scope="row" role="rowheader">{nota.numero}</th>
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
                  </tr>
                  {detalheAberto && <tr role="row" className="linha-detalhe" id={`detalhe-${nota.chave}`}>
                    <td role="cell" colSpan={4}>
                      {detalheAberto.tipo === "itens"
                        ? <ItensNota chave={nota.chave} items={detalheAberto.itens ?? []} />
                        : <EventosNota detalhe={nota.detalhe} tipo={detalheAberto.tipo} />}
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
