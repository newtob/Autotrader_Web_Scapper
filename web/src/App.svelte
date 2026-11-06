<script lang="ts">
  import { onMount } from 'svelte';
  import { fetchStats, type StatsResponse } from './lib/api';
  import { formatDate } from './lib/format';
  import ModelCard from './lib/ModelCard.svelte';

  let data = $state<StatsResponse | null>(null);
  let failed = $state(false);

  async function load() {
    failed = false;
    data = null;
    try {
      data = await fetchStats();
    } catch {
      failed = true;
    }
  }

  onMount(load);
</script>

<main>
  <header class="top">
    <div class="prompt" aria-hidden="true">
      <span class="spark">✦</span>
      <span>Vehicle prices · daily average</span>
      <span class="cursor"></span>
    </div>
    <h1>Used car price gallery</h1>
    <p class="sub">
      Autotrader is scraped every day for used cars that fit the description.
      {#if data}Data as of {formatDate(data.generated_at.slice(0, 10))}.{/if}
    </p>
  </header>

  {#if failed}
    <div class="msg" role="alert">
      <p>Couldn't load the latest prices. Try again later.</p>
      <button type="button" onclick={load}>Retry</button>
    </div>
  {:else if !data}
    <div class="skeleton" aria-busy="true" aria-label="Loading prices"></div>
    <div class="skeleton" aria-hidden="true"></div>
  {:else}
    <section class="gallery">
      {#each data.searches as search, i (search.make + search.model + (search.model_type ?? ''))}
        <ModelCard {search} index={i} />
      {/each}
    </section>
  {/if}
</main>

<style>
  main {
    max-width: 1120px;
    margin: 0 auto;
    padding: 2.5rem 16px 5rem;
    display: grid;
    gap: 2rem;
  }
  .top {
    display: grid;
    gap: 1rem;
  }
  .prompt {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.9rem 1.2rem;
    border: 1px solid var(--line);
    border-radius: 999px;
    background: var(--card);
    color: var(--muted);
    font-size: 0.95rem;
  }
  .spark {
    background: linear-gradient(120deg, var(--violet), var(--magenta), var(--amber));
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
  }
  .cursor {
    width: 2px;
    height: 1.1em;
    background: var(--cyan);
    animation: blink 1.1s steps(2) infinite;
  }
  @media (prefers-reduced-motion: reduce) {
    .cursor {
      animation: none;
    }
  }
  @keyframes blink {
    50% {
      opacity: 0;
    }
  }
  h1 {
    margin: 0;
    font-size: clamp(2.2rem, 7vw, 4.2rem);
    line-height: 1.02;
    letter-spacing: -0.04em;
    background: linear-gradient(120deg, var(--ink) 30%, var(--violet) 70%, var(--cyan));
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
  }
  .sub {
    margin: 0;
    color: var(--muted);
    max-width: 60ch;
  }
  .gallery {
    display: grid;
    gap: 1.75rem;
  }
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
