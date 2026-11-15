type Evento = { codigo: string; descricao: string; data: string; protocolo: string; nsu: string; correcao: string };
type Cte = { chave: string; status: string; transportadora: string; data: string };
type Nota = { chave: string; numero: number; serie: number; detalhe: DetalheNota };
export type DetalheNota = { cancelada: boolean; eventos: Evento[]; ctes: Cte[] };
export type ResultadoConsulta = { importadoEm: string | null; notas: Nota[]; mais: boolean };

export function EventosNota({ detalhe }: { detalhe: DetalheNota }) {
  const { eventos, ctes } = detalhe;

  return <div className="detalhe-grid">
    <section>
      <h2>Eventos ({eventos.length})</h2>
      {eventos.length === 0 && <p>Sem eventos registrados.</p>}
      {eventos.length > 0 && <ul>{eventos.map((evento, index) => <li key={index}>
        <strong>{evento.descricao || evento.codigo}</strong> · {evento.data}<br />
        {evento.protocolo && <>Protocolo {evento.protocolo} </>}
        {evento.nsu && <>· NSU {evento.nsu}</>}
        {evento.correcao && <p className="texto-evento evento-correcao">{evento.correcao}</p>}
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
  </div>;
}
