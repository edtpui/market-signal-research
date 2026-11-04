// A small SVG sparkline (the terminal's book cards): last value against the first, coloured by direction.
export function Sparkline({ points, width = 96, height = 26 }: { points: [string, number][]; width?: number; height?: number }) {
  const v = points.map((p) => p[1]).filter((x) => Number.isFinite(x));
  if (v.length < 2) return <svg width={width} height={height} aria-hidden />;
  const lo = Math.min(...v);
  const hi = Math.max(...v);
  const span = hi - lo || 1;
  const d = v.map((y, i) => `${i ? "L" : "M"}${((i / (v.length - 1)) * width).toFixed(1)},${(height - 2 - ((y - lo) / span) * (height - 4)).toFixed(1)}`).join("");
  const up = v[v.length - 1] >= v[0];
  return (
    <svg width={width} height={height} aria-hidden data-sparkline>
      <path d={d} fill="none" stroke={up ? "var(--pos)" : "var(--neg)"} strokeWidth={1.5} strokeLinejoin="round" />
    </svg>
  );
}
