"use client";

import { useId, useState } from "react";

export function SeletorArquivo({ name, accept, titulo, ajuda, multiple, disabled, onSelect }: {
  name: string; accept: string; titulo: string; ajuda: string; multiple?: boolean; disabled?: boolean;
  onSelect?: () => void;
}) {
  const descricao = useId();
  const [arquivos, setArquivos] = useState<string[]>([]);

  return <label className="arquivo-seletor seletor-arquivo">
    <input type="file" name={name} accept={accept} aria-label={titulo} aria-describedby={descricao} required multiple={multiple}
      disabled={disabled} onChange={event => {
        setArquivos(Array.from(event.currentTarget.files ?? [], file => file.name));
        onSelect?.();
      }} />
    <span className="arquivo-icone" aria-hidden="true">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6">
        <path d="M12 15V3m-4 4 4-4 4 4M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4" />
      </svg>
    </span>
    <span className="arquivo-texto">
      <strong>{arquivos.length > 1 ? `${arquivos.length} arquivos selecionados` : arquivos[0] || titulo}</strong>
      <small id={descricao} title={arquivos.length > 1 ? arquivos.join(", ") : undefined}>
        {arquivos.length > 1 ? arquivos.join(", ") : ajuda}
      </small>
    </span>
    {arquivos.length > 0 && <span className="arquivo-acao">Trocar</span>}
  </label>;
}
