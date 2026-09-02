export function formatMYT(value: string | undefined): string | undefined {
  if (!value) return undefined;
  return `${new Date(value).toLocaleString("en-MY", { timeZone: "Asia/Kuala_Lumpur", dateStyle: "medium", timeStyle: "short" })} MYT`;
}
