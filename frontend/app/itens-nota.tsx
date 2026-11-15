export type ItemNota = Record<string, string | number>;

export function ItensNota({ chave, items }: { chave: string; items: ItemNota[] }) {
  const columns = Object.keys(items[0] ?? {});
  return <>
      <a className="acao compacto" href={`/api/emissao/${chave}/itens/excel`}>Exportar Excel dos itens</a>
      {columns.length > 0 ? <div className="tabela-rolagem tabela-itens" tabIndex={0} role="region" aria-label="Itens da NF-e">
        <table className="tabela">
          <thead><tr>{columns.map(field => <th key={field} scope="col">{field}</th>)}</tr></thead>
          <tbody>{items.map((item, index) => <tr key={index}>
            {columns.map(field => <td key={field}>{item[field]}</td>)}
          </tr>)}</tbody>
        </table>
      </div> : <p>Nenhum item encontrado.</p>}
  </>;
}
