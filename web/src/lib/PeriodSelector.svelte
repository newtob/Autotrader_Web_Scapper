<script lang="ts">
  import { PERIODS, type Period } from './range';

  let { value, onchange, name }: { value: Period; onchange: (p: Period) => void; name: string } =
    $props();
</script>

<div class="group" role="group" aria-label="Time period for {name}">
  {#each PERIODS as p (p.id)}
    <button type="button" aria-pressed={value === p.id} onclick={() => onchange(p.id)}>
      {p.label}
    </button>
  {/each}
</div>

<style>
  .group {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }
  button {
    min-height: 44px;
    padding: 0 1.1rem;
    border-radius: 999px;
    border: 1px solid var(--line);
    background: transparent;
    color: var(--muted);
    font: inherit;
    font-size: 0.9rem;
    cursor: pointer;
    transition: background 0.15s, color 0.15s, border-color 0.15s;
  }
  button:hover {
    color: var(--ink);
    border-color: var(--muted);
  }
  button[aria-pressed='true'] {
    color: #0b0b0d;
    background: linear-gradient(120deg, var(--violet), var(--cyan));
    border-color: transparent;
    font-weight: 600;
  }
  button:focus-visible {
    outline: 2px solid var(--cyan);
    outline-offset: 2px;
  }
</style>
