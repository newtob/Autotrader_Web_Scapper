export interface SeriesPoint {
  date: string;
  avg_price_gbp: number | null;
  count?: number | null;
  avg_days_live?: number | null;
}

export interface Search {
  make: string;
  model: string;
  model_type?: string;
  series: SeriesPoint[];
}

/** One tracked vehicle search, as listed by `GET /api/searches`. */
export interface SearchSummary {
  make: string;
  model: string;
  type: string;
  latest_date: string;
}

/** Longest window the prices API serves; the period selector slices it client-side. */
export const MAX_DAYS = 1095;

interface Fixture {
  searches: Search[];
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} ${res.status}`);
  return (await res.json()) as T;
}

const useFixture = () => import.meta.env.DEV && new URLSearchParams(location.search).has('fixture');

export async function fetchSearches(): Promise<SearchSummary[]> {
  if (useFixture()) {
    const { default: fixture } = await import('./fixture.json');
    return (fixture as Fixture).searches.map((s) => ({
      make: s.make,
      model: s.model,
      type: s.model_type ?? '',
      latest_date: s.series.at(-1)?.date ?? '',
    }));
  }
  return (await getJson<{ searches: SearchSummary[] }>('/api/searches')).searches;
}

export async function fetchPrices(search: Pick<SearchSummary, 'make' | 'model' | 'type'>): Promise<Search> {
  if (useFixture()) {
    const { default: fixture } = await import('./fixture.json');
    const hit = (fixture as Fixture).searches.find(
      (s) => s.make === search.make && s.model === search.model && (s.model_type ?? '') === search.type,
    );
    return hit ?? { make: search.make, model: search.model, model_type: search.type, series: [] };
  }
  const params = new URLSearchParams({ make: search.make, model: search.model, days: String(MAX_DAYS) });
  if (search.type) params.set('type', search.type);
  const body = await getJson<{ make: string; model: string; type: string; series: SeriesPoint[] }>(`/api/prices?${params}`);
  return { make: body.make, model: body.model, model_type: body.type, series: body.series };
}
