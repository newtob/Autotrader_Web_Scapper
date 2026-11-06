const gbp = new Intl.NumberFormat('en-GB', {
  style: 'currency',
  currency: 'GBP',
  maximumFractionDigits: 0,
});

export const formatGbp = (n: number): string => gbp.format(n);

/** Signed whole percent with a real minus sign; 0 is unsigned. */
export function formatPercent(change: number): string {
  const rounded = Math.round(change);
  if (rounded === 0) return '0%';
  return `${rounded > 0 ? '+' : '−'}${Math.abs(rounded)}%`;
}

export function formatDate(date: string): string {
  return new Date(`${date}T00:00:00Z`).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  });
}

export const modelTitle = (s: { make: string; model: string; model_type?: string }): string =>
  [s.make, s.model, s.model_type].filter(Boolean).join(' ');
