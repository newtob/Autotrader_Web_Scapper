import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchPrices, fetchSearches } from './api';

function mockFetch(body: unknown, ok = true) {
  const fn = vi.fn().mockResolvedValue({ ok, status: ok ? 200 : 503, json: async () => body });
  vi.stubGlobal('fetch', fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe('api', () => {
  it('lists searches', async () => {
    const searches = [{ make: 'Porsche', model: 'Taycan', type: '4S', latest_date: '2026-11-05' }];
    const fn = mockFetch({ searches });
    expect(await fetchSearches()).toEqual(searches);
    expect(fn).toHaveBeenCalledWith('/api/searches');
  });

  it('url-encodes make, model and type and omits an empty type', async () => {
    const fn = mockFetch({ make: 'Hyundai', model: 'Ioniq 5', type: '', series: [] });
    await fetchPrices({ make: 'Hyundai', model: 'Ioniq 5', type: '' });
    expect(fn).toHaveBeenCalledWith('/api/prices?make=Hyundai&model=Ioniq+5&days=1095');

    const fn2 = mockFetch({ make: 'Porsche', model: 'Taycan', type: '4S & co', series: [] });
    await fetchPrices({ make: 'Porsche', model: 'Taycan', type: '4S & co' });
    expect(fn2.mock.calls[0][0]).toContain('type=4S+%26+co');
  });

  it('maps the response into a Search', async () => {
    mockFetch({ make: 'A', model: 'B', type: 'C', series: [{ date: '2026-11-05', avg_price_gbp: 1 }] });
    expect(await fetchPrices({ make: 'A', model: 'B', type: 'C' })).toEqual({
      make: 'A',
      model: 'B',
      model_type: 'C',
      series: [{ date: '2026-11-05', avg_price_gbp: 1 }],
    });
  });

  it('throws on a non-ok response', async () => {
    mockFetch({}, false);
    await expect(fetchSearches()).rejects.toThrow('503');
  });
});
