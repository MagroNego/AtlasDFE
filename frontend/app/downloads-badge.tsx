"use client";

import { useEffect, useState } from "react";

export function DownloadsBadge() {
  const [total, setTotal] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let loading = false;
    async function refresh() {
      if (loading) return;
      loading = true;
      try {
        const response = await fetch("/api/downloads/novos", { signal: controller.signal, cache: "no-store" });
        if (response.ok) {
          const data = await response.json();
          if (!controller.signal.aborted) setTotal(data.total);
        }
      } catch { /* The next refresh can recover from a network failure. */ }
      finally { loading = false; }
    }
    void refresh();
    const timer = setInterval(refresh, 7000);
    window.addEventListener("atlas-downloads-vistos", refresh);
    return () => {
      clearInterval(timer);
      window.removeEventListener("atlas-downloads-vistos", refresh);
      controller.abort();
    };
  }, []);
  return total > 0 ? <span className="downloads-badge" aria-label={`${total} novos resultados`}>{total}</span> : null;
}
