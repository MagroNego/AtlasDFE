"use client";

import { useId, useState } from "react";
import { ExportarArquivo } from "./exportar-arquivo";

export type ItemNota = Record<string, string | number>;
const metadata = new Set(["Chave Acesso", "Numero Nota", "Serie", "Data Emissao", "Emitente CNPJ", "Emitente Razao",
  "Destinatario CNPJ", "Destinatario Razao", "Destinatario UF", "Hora Emissao", "Peso Liquido", "Peso Bruto"]);

export function ItensNota({ chave, items }: { chave: string; items: ItemNota[] }) {
  const [filter, setFilter] = useState("");
  const id = useId();
  const columns = [...new Set(items.flatMap(item => Object.keys(item)))].filter(field => !metadata.has(field));
  const shown = items.filter(item => ["Codigo", "Descricao", "NCM"].some(field =>
    String(item[field] ?? "").toLocaleLowerCase("pt-BR").includes(filter.trim().toLocaleLowerCase("pt-BR"))));
  const params = new URLSearchParams({ item: filter.trim() });
  return <>
      <div className="filtro-itens">
        <label htmlFor={id}>Filtrar itens da nota<input id={id} type="search" maxLength={200} value={filter}
          onChange={event => setFilter(event.target.value)} placeholder="Descrição, código ou NCM" /></label>
        <button type="button" className="secundario" onClick={() => setFilter("")} disabled={!filter}>Limpar itens</button>
        <ExportarArquivo href={`/api/emissao/${chave}/itens/excel?${params}`}>Exportar Excel dos itens</ExportarArquivo>
        <ExportarArquivo href={`/api/emissao/${chave}/itens/csv?${params}`}>Exportar CSV dos itens</ExportarArquivo>
      </div>
      {shown.length > 0 ? <div className="tabela-rolagem tabela-itens" tabIndex={0} role="region" aria-label="Itens da NF-e">
        <table className="tabela">
          <thead><tr>{columns.map(field => <th key={field} scope="col">{field}</th>)}</tr></thead>
          <tbody>{shown.map((item, index) => <tr key={index}>
            {columns.map(field => <td key={field}>{item[field]}</td>)}
          </tr>)}</tbody>
        </table>
      </div> : <p>Nenhum item encontrado.</p>}
  </>;
}
