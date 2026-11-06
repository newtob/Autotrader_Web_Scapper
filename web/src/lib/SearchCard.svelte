<script lang="ts">
  import { fetchPrices, type Search, type SearchSummary } from './api';
  import ModelCard from './ModelCard.svelte';

  let { summary, index }: { summary: SearchSummary; index: number } = $props();

  let search = $state<Search | null>(null);
  let failed = $state(false);

  async function load() {
    failed = false;
    search = null;
    try {
      search = await fetchPrices(summary);
    } catch {
      failed = true;
    }
  }

  $effect(() => {
    void summary;
    load();
  });
</script>

{#if search}
  <ModelCard {search} {index} />
{:else if failed}
  <div class="msg" role="alert">
    <p>Couldn't load prices for {summary.make} {summary.model}{summary.type ? ` ${summary.type}` : ''}.</p>
    <button type="button" onclick={load}>Retry</button>
  </div>
{:else}
  <div class="skeleton" aria-busy="true" aria-label="Loading {summary.make} {summary.model}"></div>
{/if}

<style>
  .skeleton {
    height: 420px;
    border-radius: 28px;
    background: linear-gradient(100deg, var(--card) 30%, #1d1d24 50%, var(--card) 70%);
    background-size: 200% 100%;
    animation: shimmer 1.6s linear infinite;
  }
  @keyframes shimmer {
    to {
      background-position: -200% 0;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .skeleton {
      animation: none;
    }
  }
  .msg {
    padding: 2rem;
    border: 1px solid var(--line);
    border-radius: 20px;
    background: var(--card);
  }
  .msg button {
    min-height: 44px;
    padding: 0 1.4rem;
    border-radius: 999px;
    border: 0;
    background: linear-gradient(120deg, var(--violet), var(--cyan));
    color: #0b0b0d;
    font: inherit;
    font-weight: 600;
    cursor: pointer;
  }
</style>
