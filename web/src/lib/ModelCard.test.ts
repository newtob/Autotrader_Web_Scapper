import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it } from 'vitest';
import ModelCard from './ModelCard.svelte';
import type { Search } from './api';

const series = Array.from({ length: 40 }, (_, i) => ({
  date: new Date(Date.parse('2026-01-01') + i * 86_400_000).toISOString().slice(0, 10),
  count: 1,
  avg_price_gbp: 1000 + i * 10,
  avg_days_live: 1,
}));
const search: Search = { make: 'Porsche', model: 'Taycan', series };

afterEach(cleanup);

describe('ModelCard', () => {
  it('reloads graph and badge when the period changes', async () => {
    render(ModelCard, { search, index: 0 });
    expect(screen.getByRole('button', { name: '30 days' }).getAttribute('aria-pressed')).toBe('true');
    const before = screen.getByText(/^\+\d+%$/).textContent;
    await fireEvent.click(screen.getByRole('button', { name: '7 days' }));
    expect(screen.getByRole('button', { name: '7 days' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByText(/^\+\d+%$/).textContent).not.toBe(before);
  });

  it('shows empty state with no data', () => {
    render(ModelCard, { search: { ...search, series: [] }, index: 0 });
    expect(screen.getByText(/Collecting data/)).toBeTruthy();
  });

  it('notes short history on long periods', async () => {
    render(ModelCard, { search, index: 0 });
    await fireEvent.click(screen.getByRole('button', { name: '1 year' }));
    expect(screen.getByText(/Data since 1 Jan 2026/)).toBeTruthy();
  });
});
