/**
 * Time charts of one flight (power, current, voltage, speeds, altitude, vibration) drawn as
 * light SVG with the flight phases shaded behind the lines, one y-axis per chart, a crosshair
 * readout on hover or with the arrow keys, and the per-phase numbers in the table under the
 * timeline (so no value is hover-only).
 */
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent as ReactPointerEvent, RefObject } from 'react';
import type { FlightSeries, SeriesChannel } from '../../api/flightData';
import { niceTicks } from '../../components/Charts';
import { formatClock, linePath, phaseFamily } from '../../lib/flightData';

interface SeriesSpec {
  key: string;
  label: string;
  /** CSS class choosing the colour slot. */
  slot: 1 | 2 | 3;
  faint?: boolean;
}

interface ChartSpec {
  id: string;
  title: string;
  unit: string;
  series: SeriesSpec[];
  zeroBased?: boolean;
  explain: string;
  /** Horizontal reference lines. */
  refs?: Array<{ y: number; label: string }>;
}

function chartSpecs(series: FlightSeries, vibeWarn: number | null): ChartSpec[] {
  const has = (k: string) => k in series.channels;
  const specs: ChartSpec[] = [
    {
      id: 'power',
      title: 'Battery power',
      unit: 'W',
      zeroBased: true,
      series: [
        { key: 'power_w', label: 'Average', slot: 1 },
        { key: 'power_w_max', label: 'Peak', slot: 1, faint: true },
      ],
      explain: 'Volts × amps from the battery monitor. Hover needs the most power; the wing flight the least.',
    },
    {
      id: 'current',
      title: 'Battery current',
      unit: 'A',
      zeroBased: true,
      series: [{ key: 'current_a', label: 'Current', slot: 1 }],
      explain: 'Current drawn from the pack. Compare with the battery’s and ESCs’ continuous ratings.',
    },
    {
      id: 'voltage',
      title: 'Battery voltage',
      unit: 'V',
      series: [
        { key: 'voltage_v', label: 'Average', slot: 1 },
        { key: 'voltage_v_min', label: 'Lowest', slot: 2 },
      ],
      explain: 'Pack voltage under load. It dips (sags) when the power rises and falls slowly as the pack empties.',
    },
    {
      id: 'speed',
      title: 'Speed',
      unit: 'm/s',
      zeroBased: true,
      series: [
        { key: 'airspeed_mps', label: 'Airspeed', slot: 1 },
        { key: 'groundspeed_mps', label: 'Ground speed (GPS)', slot: 2 },
      ],
      explain: 'Airspeed is what the wing feels; ground speed adds the wind. Comparisons use airspeed.',
    },
    {
      id: 'altitude',
      title: 'Altitude above home',
      unit: 'm',
      series: [{ key: 'alt_m', label: 'Altitude', slot: 1 }],
      explain: 'Height above the take-off point.',
    },
    {
      id: 'vibration',
      title: 'Vibration',
      unit: 'm/s²',
      zeroBased: true,
      series: [
        { key: 'vibe_x', label: 'X', slot: 1 },
        { key: 'vibe_y', label: 'Y', slot: 2 },
        { key: 'vibe_z', label: 'Z', slot: 3 },
      ],
      refs: vibeWarn ? [{ y: vibeWarn, label: `ArduPilot warning level ${vibeWarn} m/s²` }] : [],
      explain:
        'Accelerometer vibration levels. ArduPilot recommends staying below about 30 m/s²; higher values upset the position estimate.',
    },
  ];
  return specs
    .map((s) => ({ ...s, series: s.series.filter((x) => has(x.key)) }))
    .filter((s) => s.series.length > 0);
}

const H = 180;
const M = { top: 14, right: 12, bottom: 30, left: 44 };

function useWidth(): [RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(520);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver((entries) => {
      const w = Math.round(entries[0]?.contentRect.width ?? 0);
      if (w > 0) setWidth(w);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

function fmt(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  return v.toLocaleString('en-IE', { maximumFractionDigits: Math.abs(v) < 10 ? 2 : Math.abs(v) < 100 ? 1 : 0 });
}

function TimeChart({ spec, series }: { spec: ChartSpec; series: FlightSeries }) {
  const [ref, measured] = useWidth();
  const W = Math.max(260, measured);
  const titleId = useId();
  const [active, setActive] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const t = series.t_s;
  const lines = spec.series.map((s) => ({ spec: s, ch: series.channels[s.key] as SeriesChannel }));
  const ys = lines.flatMap((l) => l.ch.values.filter((v): v is number => v !== null && Number.isFinite(v)));
  const refs = spec.refs ?? [];
  const yLo = Math.min(...ys, ...(spec.zeroBased ? [0] : []));
  const yHi = Math.max(...ys, ...refs.map((r) => r.y));
  const pad = spec.zeroBased ? 0 : (yHi - yLo) * 0.08;
  const yt = niceTicks(yLo - pad, yHi + pad, 4);
  const tMax = t.length ? t[t.length - 1] : 1;
  const xt = niceTicks(0, tMax, W < 420 ? 4 : 7);
  const X = (x: number) => M.left + (x / (xt.max || 1)) * (W - M.left - M.right);
  const Y = (y: number) => H - M.bottom - ((y - yt.min) / (yt.max - yt.min || 1)) * (H - M.top - M.bottom);
  if (ys.length === 0) return null;

  const nearest = (clientX: number) => {
    const svg = svgRef.current;
    if (!svg || t.length === 0) return null;
    const rect = svg.getBoundingClientRect();
    const sx = ((clientX - rect.left) / rect.width) * W;
    const x = ((sx - M.left) / (W - M.left - M.right)) * (xt.max || 1);
    let best = 0;
    for (let i = 1; i < t.length; i++) if (Math.abs(t[i] - x) < Math.abs(t[best] - x)) best = i;
    return best;
  };
  const onMove = (e: ReactPointerEvent<SVGSVGElement>) => setActive(nearest(e.clientX));
  const onKey = (e: KeyboardEvent<SVGSVGElement>) => {
    const step = Math.max(1, Math.round(t.length / 100));
    if (e.key === 'ArrowRight') setActive((a) => Math.min(t.length - 1, (a ?? -1) + step));
    else if (e.key === 'ArrowLeft') setActive((a) => Math.max(0, (a ?? t.length) - step));
    else if (e.key === 'Escape') setActive(null);
    else return;
    e.preventDefault();
  };
  const phaseAt = (time: number) => series.phases.find((p) => time >= p.start_s && time <= p.end_s);
  const a = active !== null ? active : null;
  const tipLeftPct = a !== null ? (X(t[a]) / W) * 100 : 0;

  return (
    <figure className="chart flight-chart" data-testid={`flight-chart-${spec.id}`}>
      <figcaption className="chart-title">
        {spec.title} ({spec.unit})
        <span className="flight-chart-legend small muted">
          {lines.length > 1
            ? lines.map((l) => (
                <span key={l.spec.key} className={`flight-legend-item slot-${l.spec.slot}${l.spec.faint ? ' faint-line' : ''}`}>
                  <span className="flight-legend-swatch" aria-hidden="true" />
                  {l.spec.label}
                </span>
              ))
            : null}
        </span>
      </figcaption>
      <p className="small muted flight-chart-explain">{spec.explain}</p>
      <div className="chart-plot" ref={ref}>
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
          <title id={titleId}>{`${spec.title} over the flight, in ${spec.unit}, with the flight phases shaded`}</title>
          {series.phases.map((p, i) => (
            <rect
              key={`${p.key}-${i}`}
              className={`flight-band band-${phaseFamily(p.key)}`}
              x={X(p.start_s)}
              y={M.top}
              width={Math.max(0, X(p.end_s) - X(p.start_s))}
              height={H - M.top - M.bottom}
            />
          ))}
          {yt.ticks.map((tick) => (
            <g key={`y${tick}`}>
              <line className="chart-grid" x1={M.left} x2={W - M.right} y1={Y(tick)} y2={Y(tick)} />
              <text className="chart-tick" x={M.left - 6} y={Y(tick)} dy="0.32em" textAnchor="end">
                {fmt(tick)}
              </text>
            </g>
          ))}
          {xt.ticks.map((tick) => (
            <text key={`x${tick}`} className="chart-tick" x={X(tick)} y={H - M.bottom + 14} textAnchor="middle">
              {formatClock(tick)}
            </text>
          ))}
          <line className="chart-axis" x1={M.left} x2={W - M.right} y1={H - M.bottom} y2={H - M.bottom} />
          <text className="chart-axis-label" x={(M.left + W - M.right) / 2} y={H - 3} textAnchor="middle">
            Time into the log (min:s)
          </text>
          {refs.map((r) => (
            <g key={r.label} className="chart-ref chart-ref-warning">
              <line x1={M.left} x2={W - M.right} y1={Y(r.y)} y2={Y(r.y)} />
              <text x={W - M.right - 4} y={Y(r.y) - 4} textAnchor="end">
                {r.label}
              </text>
            </g>
          ))}
          {lines.map((l) => (
            <path
              key={l.spec.key}
              className={`flight-line slot-${l.spec.slot}${l.spec.faint ? ' faint-line' : ''}`}
              d={linePath(t, l.ch.values, X, Y)}
            />
          ))}
          {a !== null ? (
            <g pointerEvents="none">
              <line className="chart-crosshair" x1={X(t[a])} x2={X(t[a])} y1={M.top} y2={H - M.bottom} />
              {lines.map((l) => {
                const v = l.ch.values[a];
                return v !== null && v !== undefined && Number.isFinite(v) ? (
                  <circle key={l.spec.key} className={`flight-dot slot-${l.spec.slot}`} cx={X(t[a])} cy={Y(v)} r={4} />
                ) : null;
              })}
            </g>
          ) : null}
        </svg>
        {a !== null ? (
          <div
            className="chart-tooltip"
            role="status"
            style={{ left: `${tipLeftPct}%`, transform: `translateX(${tipLeftPct > 60 ? '-105%' : '5%'})` }}
          >
            {lines.map((l) => (
              <strong key={l.spec.key}>
                {lines.length > 1 ? `${l.spec.label}: ` : ''}
                {fmt(l.ch.values[a])} {spec.unit}
              </strong>
            ))}
            <span className="chart-tooltip-sub">
              at {formatClock(t[a])}
              {phaseAt(t[a]) ? ` · ${phaseAt(t[a])?.label}` : ''}
            </span>
          </div>
        ) : null}
      </div>
    </figure>
  );
}

export function FlightCharts({ series, vibeWarn }: { series: FlightSeries; vibeWarn: number | null }) {
  const specs = useMemo(() => chartSpecs(series, vibeWarn), [series, vibeWarn]);
  if (specs.length === 0) return <p className="small muted">This log has no chartable channels.</p>;
  return (
    <div className="flight-chart-grid" data-testid="flight-charts">
      {specs.map((s) => (
        <TimeChart key={s.id} spec={s} series={series} />
      ))}
      <p className="small faint flight-chart-note">
        Each point is the average over {series.interval_s.toLocaleString('en-IE', { maximumFractionDigits: 1 })} s of the log (the
        autopilot records more often); peaks and the lowest voltage are kept separately.
      </p>
    </div>
  );
}
