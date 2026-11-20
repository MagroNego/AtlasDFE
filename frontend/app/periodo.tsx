"use client";

export function mesAtual(now = new Date()) {
  const parts = new Intl.DateTimeFormat("pt-BR", {
    timeZone: "America/Sao_Paulo", year: "numeric", month: "2-digit",
  }).formatToParts(now);
  const year = parts.find(part => part.type === "year")!.value;
  const month = parts.find(part => part.type === "month")!.value;
  const last = new Date(Date.UTC(Number(year), Number(month), 0)).getUTCDate();
  return { inicio: `01/${month}/${year}`, fim: `${last}/${month}/${year}` };
}

function iso(value: string) {
  const [day, month, year] = value.split("/");
  if (!/^\d{2}\/\d{2}\/\d{4}$/.test(value) || Number(year) < 1) throw new Error("Digite a data no formato DD/MM/AAAA.");
  const date = `${year}-${month}-${day}`;
  const parsed = new Date(`${date}T00:00:00Z`);
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date)
    throw new Error("Confira as datas. O dia deve existir no mês informado.");
  return date;
}

export function periodoParam(form = new FormData()) {
  const params = new URLSearchParams();
  for (const [name, field] of form) {
    if (typeof field !== "string" || !field.trim()) continue;
    const value = field.trim();
    params.set(name, name === "inicio" || name === "fim" ? iso(value) : name === "chave" ? value.replace(/[\s.-]/g, "") : value);
  }
  const start = params.get("inicio");
  const end = params.get("fim");
  if (start && end && start > end) throw new Error("A data inicial deve ser anterior ou igual à final.");
  return params;
}

export function mesParametro() {
  const form = new FormData();
  const { inicio, fim } = mesAtual();
  form.set("inicio", inicio);
  form.set("fim", fim);
  return periodoParam(form).toString();
}

export function CampoData({ name, label }: { name: "inicio" | "fim"; label: string }) {
  return <label>{label}<input name={name} defaultValue={mesAtual()[name]} inputMode="numeric" placeholder="DD/MM/AAAA"
    onInput={event => {
      const input = event.currentTarget;
      const offset = input.value.slice(0, input.selectionStart ?? undefined).replace(/\D/g, "").length;
      const digits = input.value.replace(/\D/g, "").slice(0, 8);
      const value = [digits.slice(0, 2), digits.slice(2, 4), digits.slice(4)].filter(Boolean).join("/");
      if (input.value === value) return;
      input.value = value;
      const cursor = offset >= digits.length ? value.length : offset + Number(offset > 2) + Number(offset > 4);
      input.setSelectionRange(cursor, cursor);
    }} /></label>;
}
