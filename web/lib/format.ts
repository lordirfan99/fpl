export function formatMYT(value: string | number | Date | undefined | null): string | undefined {
  if (value === undefined || value === null || value === "") return undefined;
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return undefined;
  // Pin the timezone so the server (UTC on Netlify) and the browser render the
  // SAME string — an unpinned toLocaleString is the classic Next.js hydration
  // mismatch (React #418/#425 -> #412).
  return `${d.toLocaleString("en-MY", { timeZone: "Asia/Kuala_Lumpur", dateStyle: "medium", timeStyle: "short" })} MYT`;
}
