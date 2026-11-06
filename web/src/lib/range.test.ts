import { describe, expect, it } from 'vitest';
import type { SeriesPoint } from './api';
import { percentChange, sliceWindow } from './range';
import { formatPercent } from './format';

const pt = (date: string, price: number | null): SeriesPoint => ({
  date,
  count: 1,
  avg_price_gbp: price,
  avg_days_live: 1,
});

const days = (n: number, start = '2026-01-01'): SeriesPoint[] =>
  Array.from({ length: n }, (_, i) =>
    pt(new Date(Date.parse(start) + i * 86_400_000).toISOString().slice(0, 10), 1000 + i),
  );

describe('sliceWindow', () => {
  it('anchors on the latest date and covers N days inclusive', () => {
    const w = sliceWindow(days(40), '7d');
    expect(w.points).toHaveLength(7);
    expect(w.points.at(-1)!.date).toBe('2026-02-09');
    expect(w.since).toBeNull();
  });

  it('uses all points and reports the first date when history is short', () => {
    const w = sliceWindow(days(10), '1y');
    expect(w.points).toHaveLength(10);
    expect(w.since).toBe('2026-01-01');
  });

  it('skips null prices', () => {
    const w = sliceWindow([pt('2026-01-01', 100), pt('2026-01-02', null), pt('2026-01-03', 110)], '7d');
    expect(w.points.map((p) => p.date)).toEqual(['2026-01-01', '2026-01-03']);
  });

  it('handles empty series', () => {
    expect(sliceWindow([], '30d')).toEqual({ points: [], since: null });
  });
});

describe('percentChange', () => {
  const p = (...prices: number[]) => prices.map((price, i) => ({ date: `d${i}`, price }));
  it('rising / falling / flat', () => {
    expect(percentChange(p(100, 106))).toBeCloseTo(6);
    expect(percentChange(p(100, 90))).toBeCloseTo(-10);
    expect(percentChange(p(100, 100))).toBe(0);
  });
  it('null for <2 points or zero start', () => {
    expect(percentChange(p(100))).toBeNull();
    expect(percentChange(p(0, 5))).toBeNull();
  });
});

describe('formatPercent', () => {
  it('signs with real minus and leaves zero bare', () => {
    expect(formatPercent(6.2)).toBe('+6%');
    expect(formatPercent(-3.4)).toBe('−3%');
    expect(formatPercent(0.2)).toBe('0%');
  });
});
