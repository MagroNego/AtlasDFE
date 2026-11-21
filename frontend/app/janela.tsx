"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";

export function Janela({ titulo, busy, onClose, children }: {
  titulo: string; busy?: boolean; onClose: () => void; children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const heading = useId();
  useEffect(() => { dialog.current?.showModal(); }, []);

  return <dialog className="janela-atlas" aria-labelledby={heading} ref={dialog}
    onClose={onClose} onCancel={event => { if (busy) event.preventDefault(); }}>
    <header className="janela-cabecalho">
      <h2 id={heading}>{titulo}</h2>
      <button type="button" className="secundario controle-icone" aria-label="Fechar janela" title="Fechar"
        disabled={busy} onClick={() => dialog.current?.close()}>
        <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
          <path d="m6 6 12 12M18 6 6 18" />
        </svg>
      </button>
    </header>
    {children}
  </dialog>;
}
