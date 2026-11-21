"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ExportarArquivo } from "../../exportar-arquivo";

type Batch = { id: number; usuario: string; criadoEm: string; notas: number; cancelamentos: number; cce: number; duplicatas: number; erros: number };

export default function Importacoes() {
  const [batches, setBatches] = useState<Batch[]>();
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch("/api/admin/importacoes", { signal: controller.signal });
        if (!response.ok) throw new Error();
        setBatches(await response.json());
      } catch {
        if (!controller.signal.aborted) setError(true);
      }
    })();
    return () => controller.abort();
  }, []);

  return <section className="tela-fiscal">
    <div className="titulo-fiscal"><h1>Importações XML</h1><Link className="acao" href="/emissao">Emissão</Link></div>
    <section className="painel-fiscal">
      {error ? <p role="alert">Não foi possível carregar as importações.</p> : !batches ? <p role="status">Carregando…</p> :
        batches.length === 0 ? <p>Nenhum lote registrado.</p> :
          <div className="tabela-rolagem" tabIndex={0}><table className="tabela">
            <thead><tr>{["Lote", "Data", "Usuário", "Notas", "Cancelamentos", "CC-e", "Duplicatas", "Erros", "Relatório"].map(label =>
              <th key={label} scope="col">{label}</th>)}</tr></thead>
            <tbody>{batches.map(batch => <tr key={batch.id}>
              <td>{batch.id}</td><td>{new Date(batch.criadoEm).toLocaleString("pt-BR")}</td><td>{batch.usuario}</td>
              <td>{batch.notas}</td><td>{batch.cancelamentos}</td><td>{batch.cce}</td><td>{batch.duplicatas}</td><td>{batch.erros}</td>
              <td><ExportarArquivo href={`/api/admin/importacoes/${batch.id}/relatorio`}>Gerar CSV</ExportarArquivo></td>
            </tr>)}</tbody>
          </table></div>}
    </section>
  </section>;
}
