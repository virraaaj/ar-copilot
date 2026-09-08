// Standardized formatting for money and timestamps across the app.
// Adopted conventions:
//   - Money: whole dollars (USD, no cents)
//   - Timestamp: medium date + short time (e.g. "Sep 7, 2026, 3:45 PM")

export function formatMoney(n: number | null): string {
  if (n === null) return "--";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "--";
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
