import type { SeriesPoint } from './api';

export type Period = '7d' | '30d' | '1y' | '3y';

export const PERIODS: { id: Period; label: string; days: number }[] = [
  { id: '7d', label: '7 days', days: 7 },
  { id: '30d', label: '30 days', days: 30 },
  { id: '1y', label: '1 year', days: 365 },
  { id: '3y', label: '3 years', days: 1095 },
];

export interface PricePoint {
  date: string;
  price: number;
}

export interface Window {
  points: PricePoint[];
  /** First date in the window when history is shorter than the period, else null. */
  since: string | null;
}

const DAY_MS = 86_400_000;

function toDay(date: string): number {
  return Date.parse(`${date}T00:00:00Z`);
}

/** Last `days` days ending at the latest priced date (inclusive). Null prices are skipped. */
export function sliceWindow(series: SeriesPoint[], period: Period): Window {
  const priced = series
    .filter((p): p is SeriesPoint & { avg_price_gbp: number } => p.avg_price_gbp != null)
    .map((p) => ({ date: p.date, price: p.avg_price_gbp }))
    .sort((a, b) => a.date.localeCompare(b.date));
  if (priced.length === 0) return { points: [], since: null };

  const days = PERIODS.find((p) => p.id === period)!.days;
  const end = toDay(priced[priced.length - 1].date);
  const start = end - (days - 1) * DAY_MS;
  const points = priced.filter((p) => toDay(p.date) >= start);
  const short = toDay(priced[0].date) > start;
  return { points, since: short ? priced[0].date : null };
}

/** Percent change first→last, or null with fewer than two points or a zero start. */
export function percentChange(points: PricePoint[]): number | null {
  if (points.length < 2) return null;
  const first = points[0].price;
  if (first === 0) return null;
  return ((points[points.length - 1].price - first) / first) * 100;
}
