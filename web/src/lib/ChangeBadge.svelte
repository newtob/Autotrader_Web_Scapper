<script lang="ts">
  import { formatPercent } from './format';

  let { change, label }: { change: number | null; label: string } = $props();

  const rounded = $derived(change === null ? null : Math.round(change));
  const dir = $derived(rounded === null || rounded === 0 ? 'flat' : rounded > 0 ? 'up' : 'down');
  const arrow = $derived(dir === 'up' ? '▲' : dir === 'down' ? '▼' : '■');
</script>

<div class="badge {dir}">
  {#if change === null}
    <span class="pct">–</span>
    <span class="label">not enough data</span>
  {:else}
    <span class="arrow" aria-hidden="true">{arrow}</span>
    <span class="pct">{formatPercent(change)}</span>
    <span class="label">{label}</span>
  {/if}
</div>

<style>
  .badge {
    display: inline-flex;
    align-items: baseline;
    gap: 0.5rem;
    flex-wrap: wrap;
  }
  .pct {
    font-size: clamp(2.25rem, 8vw, 3.5rem);
    font-weight: 700;
    letter-spacing: -0.03em;
    line-height: 1;
    font-variant-numeric: tabular-nums;
  }
  .arrow {
    font-size: 0.9rem;
  }
  .label {
    color: var(--muted);
    font-size: 0.9rem;
  }
  .up .pct,
  .up .arrow {
    color: var(--gain);
  }
  .down .pct,
  .down .arrow {
    color: var(--loss);
  }
  .flat .pct,
  .flat .arrow {
    color: var(--ink);
  }
</style>
