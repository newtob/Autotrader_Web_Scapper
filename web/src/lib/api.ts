export interface SeriesPoint {
  date: string;
  count: number | null;
  avg_price_gbp: number | null;
  avg_days_live: number | null;
}

export interface Search {
  make: string;
  model: string;
  model_type?: string;
  series: SeriesPoint[];
}

export interface StatsResponse {
  generated_at: string;
  searches: Search[];
}

export async function fetchStats(): Promise<StatsResponse> {
  if (import.meta.env.DEV && new URLSearchParams(location.search).has('fixture')) {
    return (await import('./fixture.json')).default as StatsResponse;
  }
  const res = await fetch('/api/stats');
  if (!res.ok) throw new Error(`stats ${res.status}`);
  return (await res.json()) as StatsResponse;
}
