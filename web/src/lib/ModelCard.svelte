<script lang="ts">
  import type { Search } from './api';
  import ChangeBadge from './ChangeBadge.svelte';
  import { formatDate, formatGbp, modelTitle } from './format';
  import PeriodSelector from './PeriodSelector.svelte';
  import PriceGraph from './PriceGraph.svelte';
  import { PERIODS, percentChange, sliceWindow, type Period } from './range';

  let { search, index }: { search: Search; index: number } = $props();

  let period = $state<Period>('30d');

  const title = $derived(modelTitle(search));
  const win = $derived(sliceWindow(search.series, period));
  const change = $derived(percentChange(win.points));
  const label = $derived(`average price, last ${PERIODS.find((p) => p.id === period)!.label}`);
  const latest = $derived(win.points.at(-1));
  // Each card gets its own accent hue so the gallery reads as a set of distinct "styles".
  const hue = $derived(`--card-hue: ${(index * 67 + 255) % 360}`);
</script>

<article class="card" style={hue} aria-labelledby="t-{index}">
  <header>
    <div>
      <p class="kicker">{search.make}</p>
      <h2 id="t-{index}">{title}</h2>
    </div>
    {#if latest}
      <p class="now"><span>Latest average</span>{formatGbp(latest.price)}</p>
    {/if}
  </header>

  {#if win.points.length === 0}
    <p class="empty">Collecting data — first point arrives after the next daily run.</p>
  {:else}
    <ChangeBadge {change} {label} />
    <PriceGraph points={win.points} name={title} {change} id={String(index)} />
    {#if win.since}
      <p class="note">Data since {formatDate(win.since)}</p>
    {/if}
  {/if}

  <PeriodSelector value={period} onchange={(p) => (period = p)} name={title} />
</article>

<style>
  .card {
    position: relative;
    overflow: hidden;
    display: grid;
    gap: 1.25rem;
    padding: clamp(1.1rem, 3vw, 2rem);
    border-radius: 28px;
    border: 1px solid var(--line);
    background:
      radial-gradient(60% 70% at 100% 0%, hsl(var(--card-hue) 90% 60% / 0.18), transparent 70%),
      radial-gradient(50% 60% at 0% 100%, hsl(calc(var(--card-hue) + 70) 90% 60% / 0.12), transparent 70%),
      var(--card);
    box-shadow: 0 30px 80px -40px hsl(var(--card-hue) 90% 55% / 0.35);
  }
  header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 1rem;
    flex-wrap: wrap;
  }
  .kicker {
    margin: 0;
    color: var(--muted);
    font-size: 0.8rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  h2 {
    margin: 0.15rem 0 0;
    font-size: clamp(1.5rem, 4vw, 2.1rem);
    letter-spacing: -0.02em;
  }
  .now {
    margin: 0;
    font-size: 1.25rem;
    font-weight: 600;
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .now span {
    display: block;
    color: var(--muted);
    font-size: 0.75rem;
    font-weight: 400;
  }
  .note,
  .empty {
    margin: 0;
    color: var(--muted);
    font-size: 0.85rem;
  }
  .empty {
    padding: 2.5rem 0;
    text-align: center;
  }
</style>
