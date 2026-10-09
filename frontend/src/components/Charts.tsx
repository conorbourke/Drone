/**
 * Small SVG charts for the analysis panel, following the dataviz guidance the app already uses
 * (docs/ARCHITECTURE.md; tokens `--series-*` from the validated reference palette):
 * one y-axis per chart (two measures are two charts, never a dual axis), 2 px lines, >= 8 px
 * markers with a 2 px surface ring, bars at most 24 px thick with a 4 px rounded data end,
 * hairline recessive grid, text in text colours, a hover/focus readout (crosshair on lines,
 * per-bar on bars) and a table view under every chart so no value is hover-only.
 */
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent as ReactPointerEvent, RefObject } from 'react';

function niceStep(span: number, target: number): number {
  if (!(span > 0)) return 1;
  const raw = span / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const n = raw / mag;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * mag;
}

/** Clean axis ticks covering [lo, hi]. */
export function niceTicks(lo: number, hi: number, target = 5): { min: number; max: number; ticks: number[] } {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return { min: 0, max: 1, ticks: [0, 1] };
  if (hi - lo < 1e-12) {
    hi = lo + (Math.abs(lo) || 1);
  }
  const step = niceStep(hi - lo, target);
  const min = Math.floor(lo / step) * step;
  const max = Math.ceil(hi / step) * step;
  const ticks: number[] = [];
  for (let t = min; t <= max + step * 1e-6; t += step) ticks.push(Number(t.toPrecision(12)));
  return { min, max, ticks };
}

function tickText(n: number): string {
  return n.toLocaleString('en-IE', { maximumFractionDigits: Math.abs(n) < 10 ? 2 : 0 });
}

export interface LinePoint {
  x: number;
  y: number;
}

export interface ReferenceLine {
  y: number;
  label: string;
  tone: 'critical' | 'warning' | 'neutral';
}

interface LineChartProps {
  points: LinePoint[];
  xLabel: string;
  xUnit: string;
  yLabel: string;
  yUnit: string;
  /** Horizontal reference lines (labelled at the right end). */
  references?: ReferenceLine[];
  /** Include zero on the y-axis (magnitudes such as power). */
  zeroBased?: boolean;
  /** Formats a y value for the readout and the table. */
  formatY?: (y: number) => string;
  formatX?: (x: number) => string;
  ariaLabel: string;
  testId?: string;
  /** Mark the point nearest this x (for example where the engine reports the minimum margin). */
  markX?: number | null;
}

const DEFAULT_W = 520;
const H = 210;

/** Width of an element in CSS pixels, so the chart is drawn 1:1 and its text stays legible. */
function useWidth(ref: RefObject<HTMLElement | null>, fallback: number): number {
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver((entries) => {
      const w = Math.round(entries[0]?.contentRect.width ?? 0);
      if (w > 0) setWidth(w);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}
const M = { top: 26, right: 16, bottom: 36, left: 44 };

export function LineChart({
  points,
  xLabel,
  xUnit,
  yLabel,
  yUnit,
  references = [],
  zeroBased,
  formatY = (y) => tickText(y),
  formatX = (x) => tickText(x),
  ariaLabel,
  testId,
  markX,
}: LineChartProps) {
  const [active, setActive] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const plotRef = useRef<HTMLDivElement>(null);
  const W = Math.max(260, useWidth(plotRef, DEFAULT_W));
  const titleId = useId();
  const data = useMemo(() => points.filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y)), [points]);

  const xs = data.map((p) => p.x);
  const ys = [...data.map((p) => p.y), ...references.map((r) => r.y)];
  const xt = niceTicks(Math.min(...xs), Math.max(...xs), W < 400 ? 4 : 6);
  const yLo = Math.min(...ys, ...(zeroBased ? [0] : []));
  const yHi = Math.max(...ys);
  const pad = zeroBased ? 0 : (yHi - yLo) * 0.08;
  const yt = niceTicks(yLo - pad, yHi + pad, 5);
  const X = (x: number) => M.left + ((x - xt.min) / (xt.max - xt.min || 1)) * (W - M.left - M.right);
  const Y = (y: number) => H - M.bottom - ((y - yt.min) / (yt.max - yt.min || 1)) * (H - M.top - M.bottom);
  if (data.length === 0) return <p className="small muted">No data to plot.</p>;

  const path = data.map((p, i) => `${i ? 'L' : 'M'}${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join(' ');
  const minIndex =
    markX !== undefined && markX !== null && Number.isFinite(markX)
      ? data.reduce((best, p, i) => (Math.abs(p.x - markX) < Math.abs(data[best].x - markX) ? i : best), 0)
      : -1;
  const lowestRef = references.length ? Math.min(...references.map((r) => r.y)) : NaN;

  const nearest = (clientX: number) => {
    const svg = svgRef.current;
    if (!svg) return null;
    const rect = svg.getBoundingClientRect();
    const sx = ((clientX - rect.left) / rect.width) * W;
    let best = 0;
    for (let i = 1; i < data.length; i++) if (Math.abs(X(data[i].x) - sx) < Math.abs(X(data[best].x) - sx)) best = i;
    return best;
  };
  const onMove = (event: ReactPointerEvent<SVGSVGElement>) => setActive(nearest(event.clientX));
  const onKey = (event: KeyboardEvent<SVGSVGElement>) => {
    if (event.key === 'ArrowRight') setActive((a) => Math.min(data.length - 1, (a ?? -1) + 1));
    else if (event.key === 'ArrowLeft') setActive((a) => Math.max(0, (a ?? data.length) - 1));
    else if (event.key === 'Escape') setActive(null);
    else return;
    event.preventDefault();
  };
  const a = active !== null ? data[active] : null;
  const tipLeftPct = a ? (X(a.x) / W) * 100 : 0;

  return (
    <figure className="chart" data-testid={testId}>
      <div className="chart-plot" ref={plotRef}>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-labelledby={titleId}
          tabIndex={0}
          onPointerMove={onMove}
          onPointerLeave={() => setActive(null)}
          onFocus={() => setActive((v) => v ?? 0)}
          onBlur={() => setActive(null)}
          onKeyDown={onKey}
        >
          <title id={titleId}>{ariaLabel}</title>
          {yt.ticks.map((t) => (
            <g key={`y${t}`}>
              <line className="chart-grid" x1={M.left} x2={W - M.right} y1={Y(t)} y2={Y(t)} />
              <text className="chart-tick" x={M.left - 6} y={Y(t)} dy="0.32em" textAnchor="end">
                {tickText(t)}
              </text>
            </g>
          ))}
          {xt.ticks.map((t) => (
            <text key={`x${t}`} className="chart-tick" x={X(t)} y={H - M.bottom + 14} textAnchor="middle">
              {tickText(t)}
            </text>
          ))}
          <line className="chart-axis" x1={M.left} x2={W - M.right} y1={H - M.bottom} y2={H - M.bottom} />
          <text className="chart-axis-label" x={(M.left + W - M.right) / 2} y={H - 4} textAnchor="middle">
            {xLabel} ({xUnit})
          </text>
          <text className="chart-axis-label" x={M.left - 6} y={M.top - 12} textAnchor="end">
            {yUnit || ''}
          </text>
          {references.map((r) => (
            <g key={r.label} className={`chart-ref chart-ref-${r.tone}`}>
              <line x1={M.left} x2={W - M.right} y1={Y(r.y)} y2={Y(r.y)} />
              <text x={M.left + 6} y={Y(r.y) + (r.y === lowestRef && references.length > 1 ? 12 : -4)} textAnchor="start">
                {r.label}
              </text>
            </g>
          ))}
          <path className="chart-line" d={path} />
          {minIndex >= 0 ? <circle className="chart-dot" cx={X(data[minIndex].x)} cy={Y(data[minIndex].y)} r={4.5} /> : null}
          {a ? (
            <g pointerEvents="none">
              <line className="chart-crosshair" x1={X(a.x)} x2={X(a.x)} y1={M.top} y2={H - M.bottom} />
              <circle className="chart-dot" cx={X(a.x)} cy={Y(a.y)} r={5} />
            </g>
          ) : null}
        </svg>
        {a ? (
          <div
            className="chart-tooltip"
            role="status"
            style={{ left: `${tipLeftPct}%`, transform: `translateX(${tipLeftPct > 60 ? '-105%' : '5%'})` }}
          >
            <strong>
              {formatY(a.y)}
              {yUnit ? ` ${yUnit}` : ''}
            </strong>
            <span className="chart-tooltip-sub">
              {yLabel} at {formatX(a.x)} {xUnit}
            </span>
          </div>
        ) : null}
      </div>
      <details className="chart-table">
        <summary>Table view</summary>
        <div className="table-scroll">
          <table className="table table-compact">
            <thead>
              <tr>
                <th className="num">
                  {xLabel} ({xUnit})
                </th>
                <th className="num">
                  {yLabel}
                  {yUnit ? ` (${yUnit})` : ''}
                </th>
              </tr>
            </thead>
            <tbody>
              {data.map((p, i) => (
                <tr key={i}>
                  <td className="num">{formatX(p.x)}</td>
                  <td className="num">{formatY(p.y)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}

export interface BarDatum {
  key: string;
  label: string;
  value: number;
  /** Extra text for the readout and the table, e.g. the duration. */
  note?: string;
}

interface BarChartProps {
  bars: BarDatum[];
  unit: string;
  valueLabel: string;
  format?: (v: number) => string;
  ariaLabel: string;
  testId?: string;
}

/** Horizontal bars from one baseline, value at the tip; per-bar hover and focus readout. */
export function BarChart({ bars, unit, valueLabel, format = (v) => tickText(v), ariaLabel, testId }: BarChartProps) {
  const [active, setActive] = useState<string | null>(null);
  const max = Math.max(...bars.map((b) => b.value), 0) || 1;
  return (
    <figure className="chart" data-testid={testId}>
      <ul className="hbar-list" aria-label={ariaLabel}>
        {bars.map((b) => (
          <li
            key={b.key}
            className={`hbar-row${active === b.key ? ' is-active' : ''}`}
            tabIndex={0}
            title={`${b.label}: ${format(b.value)} ${unit}${b.note ? ` (${b.note})` : ''}`}
            onPointerEnter={() => setActive(b.key)}
            onPointerLeave={() => setActive(null)}
            onFocus={() => setActive(b.key)}
            onBlur={() => setActive(null)}
          >
            <span className="hbar-label">{b.label}</span>
            <span className="hbar-track" aria-hidden="true">
              <span className="hbar-fill" style={{ width: `${Math.max(0.5, (b.value / max) * 100)}%` }} />
            </span>
            <span className="hbar-value">
              {format(b.value)} {unit}
              {active === b.key && b.note ? <span className="faint"> · {b.note}</span> : null}
            </span>
          </li>
        ))}
      </ul>
      <details className="chart-table">
        <summary>Table view</summary>
        <div className="table-scroll">
          <table className="table table-compact">
            <thead>
              <tr>
                <th>Phase</th>
                <th className="num">
                  {valueLabel} ({unit})
                </th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {bars.map((b) => (
                <tr key={b.key}>
                  <td>{b.label}</td>
                  <td className="num">{format(b.value)}</td>
                  <td>{b.note ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
