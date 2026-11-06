<script lang="ts">
  import { formatDate, formatGbp, formatPercent } from './format';
  import type { PricePoint } from './range';

  let {
    points,
    name,
    change,
    id,
  }: { points: PricePoint[]; name: string; change: number | null; id: string } = $props();

  const W = 640;
  const H = 260;
  const PAD = { l: 64, r: 16, t: 16, b: 32 };

  const geo = $derived.by(() => {
    const prices = points.map((p) => p.price);
    const lo = Math.min(...prices);
    const hi = Math.max(...prices);
    const span = hi - lo || Math.max(hi * 0.02, 1);
    const min = lo - span * 0.15;
    const max = hi + span * 0.15;
    const x = (i: number) =>
      points.length === 1
        ? (PAD.l + W - PAD.r) / 2
        : PAD.l + (i / (points.length - 1)) * (W - PAD.l - PAD.r);
    const y = (v: number) => PAD.t + (1 - (v - min) / (max - min)) * (H - PAD.t - PAD.b);
    const ticks = [0, 1, 2, 3].map((i) => min + ((max - min) * i) / 3);
    const xy = points.map((p, i) => [x(i), y(p.price)] as const);
    const line = xy.map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(1)},${py.toFixed(1)}`).join(' ');
    const base = H - PAD.b;
    const area = `${line} L${xy.at(-1)![0].toFixed(1)},${base} L${xy[0][0].toFixed(1)},${base} Z`;
    return { xy, ticks, y, line, area };
  });

  let active = $state<number | null>(null);
  let svg: SVGSVGElement | undefined = $state();

  function onMove(e: PointerEvent) {
    if (!svg || points.length === 0) return;
    const rect = svg.getBoundingClientRect();
    const vx = ((e.clientX - rect.left) / rect.width) * W;
    let best = 0;
    let dist = Infinity;
    geo.xy.forEach(([px], i) => {
      const d = Math.abs(px - vx);
      if (d < dist) {
        dist = d;
        best = i;
      }
    });
    active = best;
  }

  function onKey(e: KeyboardEvent) {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const cur = active ?? points.length - 1;
    active = Math.min(points.length - 1, Math.max(0, cur + (e.key === 'ArrowRight' ? 1 : -1)));
  }

  const summary = $derived(
    points.length
      ? `${name} average price from ${formatGbp(points[0].price)} on ${formatDate(points[0].date)} to ${formatGbp(points.at(-1)!.price)} on ${formatDate(points.at(-1)!.date)}${change === null ? '' : `, ${formatPercent(change)}`}`
      : `${name}: no data`,
  );
</script>

<div class="wrap">
  <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
  <svg
    bind:this={svg}
    viewBox="0 0 {W} {H}"
    role="img"
    aria-label={summary}
    tabindex="0"
    onpointermove={onMove}
    onpointerleave={() => (active = null)}
    onkeydown={onKey}
    onblur={() => (active = null)}
  >
    <defs>
      <linearGradient id="stroke-{id}" x1="0" x2="1">
        <stop offset="0" stop-color="var(--violet)" />
        <stop offset="0.55" stop-color="var(--cyan)" />
        <stop offset="1" stop-color="var(--lime)" />
      </linearGradient>
      <linearGradient id="fill-{id}" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stop-color="var(--cyan)" stop-opacity="0.35" />
        <stop offset="1" stop-color="var(--violet)" stop-opacity="0" />
      </linearGradient>
    </defs>

    {#each geo.ticks as t}
      <line class="grid" x1={PAD.l} x2={W - PAD.r} y1={geo.y(t)} y2={geo.y(t)} />
      <text class="axis" x={PAD.l - 8} y={geo.y(t) + 4} text-anchor="end">{formatGbp(t)}</text>
    {/each}

    {#if points.length > 1}
      <path d={geo.area} fill="url(#fill-{id})" />
      <path d={geo.line} fill="none" stroke="url(#stroke-{id})" stroke-width="3" stroke-linejoin="round" stroke-linecap="round" />
    {:else}
      <circle cx={geo.xy[0][0]} cy={geo.xy[0][1]} r="5" fill="var(--cyan)" />
    {/if}

    <text class="axis" x={PAD.l} y={H - 8}>{formatDate(points[0].date)}</text>
    <text class="axis" x={W - PAD.r} y={H - 8} text-anchor="end">{formatDate(points.at(-1)!.date)}</text>

    {#if active !== null}
      {@const [ax, ay] = geo.xy[active]}
      <line class="cross" x1={ax} x2={ax} y1={PAD.t} y2={H - PAD.b} />
      <circle cx={ax} cy={ay} r="5" fill="var(--ink)" stroke="var(--cyan)" stroke-width="2" />
    {/if}
  </svg>

  {#if active !== null}
    <div class="tip" style="left: {(geo.xy[active][0] / W) * 100}%">
      <strong>{formatGbp(points[active].price)}</strong>
      <span>{formatDate(points[active].date)}</span>
    </div>
  {/if}
</div>

<style>
  .wrap {
    position: relative;
  }
  svg {
    width: 100%;
    height: auto;
    display: block;
    touch-action: pan-y;
    border-radius: 12px;
  }
  svg:focus-visible {
    outline: 2px solid var(--cyan);
    outline-offset: 4px;
  }
  .grid {
    stroke: var(--line);
    stroke-width: 1;
  }
  .axis {
    fill: var(--muted);
    font-size: 11px;
  }
  .cross {
    stroke: var(--muted);
    stroke-dasharray: 3 3;
  }
  .tip {
    position: absolute;
    top: 0;
    transform: translateX(-50%);
    background: rgba(10, 10, 12, 0.92);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 0.4rem 0.7rem;
    display: flex;
    flex-direction: column;
    font-size: 0.8rem;
    pointer-events: none;
    white-space: nowrap;
  }
  .tip span {
    color: var(--muted);
  }
</style>
