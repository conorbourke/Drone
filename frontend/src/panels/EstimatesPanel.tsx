/**
 * Live Tier 1 estimates beside the design views: mass with its breakdown, balance at both
 * payloads, wing loading, stall speed, cruise/stall, cruise and hover power, endurance and
 * range as ranges, the checks (green/amber/red with plain messages) and the assumptions.
 * Every number carries the engine's explanation and source through Explain.
 */
import type { ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router';
import type { PartsMasses, Settings } from '../api/types';
import { Explain } from '../components/Explain';
import type { Estimates, MassComponent, Quantity, Status, StatusLevel } from '../engine';
import { partsMassNames } from '../lib/partsList';

/** Plain A3 rules note shown next to range and endurance (docs/BRIEF.md). */
export const A3_NOTE =
  'Visual line of sight; beyond needs IAA authorisation; at least 150 m from residential, commercial, industrial or recreational areas.';

const DIGITS: Record<string, number> = {
  kg: 2,
  mm: 0,
  '% MAC': 1,
  'kg/m²': 1,
  'm/s': 1,
  W: 0,
  min: 0,
  km: 1,
  '': 2,
};

export function formatQ(value: number, unit: string, digits?: number): string {
  if (!Number.isFinite(value)) return '–';
  const d = digits ?? DIGITS[unit] ?? 1;
  return value.toLocaleString('en-IE', { minimumFractionDigits: d, maximumFractionDigits: d });
}

/** "3.33 kg (2.98–3.69)" or, with `rangeFirst`, "29–43 min (best 36)". */
export function quantityText(q: Quantity, opts: { rangeFirst?: boolean; digits?: number } = {}): string {
  const unit = q.unit ? ` ${q.unit}` : '';
  const hasRange = Number.isFinite(q.low) && Number.isFinite(q.high) && Math.abs(q.high - q.low) > 1e-9;
  if (!hasRange) return `${formatQ(q.value, q.unit, opts.digits)}${unit}`;
  if (opts.rangeFirst) {
    return `${formatQ(q.low, q.unit, opts.digits)}–${formatQ(q.high, q.unit, opts.digits)}${unit}`;
  }
  return `${formatQ(q.value, q.unit, opts.digits)}${unit}`;
}

export function explainQuantity(q: Quantity, digits?: number): string {
  const unit = q.unit ? ` ${q.unit}` : '';
  const range =
    Number.isFinite(q.low) && Number.isFinite(q.high) && Math.abs(q.high - q.low) > 1e-9
      ? `Plausible range ${formatQ(q.low, q.unit, digits)} to ${formatQ(q.high, q.unit, digits)}${unit}; best estimate ${formatQ(q.value, q.unit, digits)}${unit}.`
      : '';
  return [q.explain, range, q.source ? `Method: ${q.source}` : ''].filter(Boolean).join('\n\n');
}

const LEVEL_TEXT: Record<StatusLevel, { label: string; icon: string; tone: string }> = {
  ok: { label: 'OK', icon: '✓', tone: 'ok' },
  warn: { label: 'Check', icon: '!', tone: 'warn' },
  fail: { label: 'Problem', icon: '✕', tone: 'error' },
  info: { label: 'Note', icon: 'i', tone: 'info' },
};

export function StatusBadge({ level }: { level: StatusLevel }) {
  const t = LEVEL_TEXT[level];
  return (
    <span className={`pill pill-${t.tone} status-badge`}>
      <span aria-hidden="true" className="status-icon">
        {t.icon}
      </span>
      {t.label}
    </span>
  );
}

/** Worst level among the statuses whose key matches. */
function worst(statuses: Status[], keys: string[]): StatusLevel | null {
  const order: StatusLevel[] = ['fail', 'warn', 'ok', 'info'];
  const hits = statuses.filter((s) => keys.some((k) => s.key === k || s.key.startsWith(`${k}_`)));
  if (hits.length === 0) return null;
  return order.find((level) => hits.some((s) => s.level === level)) ?? null;
}

function Metric({
  id,
  q,
  label,
  rangeFirst,
  digits,
  level,
  extra,
}: {
  id: string;
  q: Quantity;
  label?: string;
  rangeFirst?: boolean;
  digits?: number;
  level?: StatusLevel | null;
  extra?: ReactNode;
}) {
  const name = label ?? q.label;
  return (
    <div className="metric" data-testid={`estimate-${id}`} data-value={Number.isFinite(q.value) ? q.value : ''}>
      <div className="metric-head">
        <span className="metric-label">{name}</span>
        <Explain label={name} text={explainQuantity(q, digits)} />
        {level && level !== 'info' ? (
          <span className={`metric-dot metric-dot-${level}`} title={LEVEL_TEXT[level].label} aria-label={LEVEL_TEXT[level].label} />
        ) : null}
      </div>
      <div className="metric-value">{quantityText(q, { rangeFirst, digits })}</div>
      {rangeFirst && Number.isFinite(q.value) ? (
        <div className="metric-sub">best estimate {formatQ(q.value, q.unit, digits)} {q.unit}</div>
      ) : !rangeFirst && Number.isFinite(q.low) && Math.abs(q.high - q.low) > 1e-9 ? (
        <div className="metric-sub">
          range {formatQ(q.low, q.unit, digits)}–{formatQ(q.high, q.unit, digits)}
        </div>
      ) : null}
      {extra}
    </div>
  );
}

const GROUP_LABELS: Record<MassComponent['group'], string> = {
  structure: 'Structure',
  propulsion: 'Motors, ESCs and propellers',
  energy: 'Battery',
  systems: 'Systems and avionics',
  payload: 'Payload (heaviest camera)',
};

function MassBreakdown({ components, total }: { components: MassComponent[]; total: number }) {
  const groups = (Object.keys(GROUP_LABELS) as MassComponent['group'][])
    .map((group) => ({
      group,
      items: components.filter((c) => c.group === group).sort((a, b) => b.mass_g - a.mass_g),
    }))
    .map((g) => ({ ...g, mass: g.items.reduce((sum, c) => sum + c.mass_g, 0) }))
    .filter((g) => g.items.length > 0)
    .sort((a, b) => b.mass - a.mass);
  const max = Math.max(...groups.map((g) => g.mass), 1);
  return (
    <details className="breakdown" data-testid="mass-breakdown">
      <summary>Mass breakdown</summary>
      <ul className="bar-list" aria-label="Mass by group">
        {groups.map((g) => (
          <li key={g.group} className="bar-row" title={`${GROUP_LABELS[g.group]}: ${Math.round(g.mass)} g`}>
            <span className="bar-label">{GROUP_LABELS[g.group]}</span>
            <span className="bar-track" aria-hidden="true">
              <span className="bar-fill" style={{ width: `${(g.mass / max) * 100}%` }} />
            </span>
            <span className="bar-value">
              {Math.round(g.mass)} g <span className="faint">({Math.round((g.mass / (total * 1000)) * 100)} %)</span>
            </span>
          </li>
        ))}
      </ul>
      <div className="table-scroll">
        <table className="table table-compact">
          <thead>
            <tr>
              <th>Item</th>
              <th className="num">Mass</th>
              <th className="num">x from nose</th>
              <th aria-label="Explanation" />
            </tr>
          </thead>
          <tbody>
            {[...components]
              .sort((a, b) => b.mass_g - a.mass_g)
              .map((c) => (
                <tr key={c.key} data-from-parts={c.from_parts ? 'true' : undefined}>
                  <td>
                    {c.label}
                    {c.from_parts ? (
                      <span className="pill pill-info parts-mass-pill" title="Mass of the part chosen on the Parts tab">
                        part
                      </span>
                    ) : null}
                  </td>
                  <td className="num">{Math.round(c.mass_g)} g</td>
                  <td className="num">{Math.round(c.x_mm)} mm</td>
                  <td>
                    <Explain
                      label={c.label}
                      text={[c.explain, `±${Math.round(c.uncertainty * 100)} % uncertainty.`, `Source: ${c.source}`].join('\n\n')}
                    />
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

/**
 * Balance strip along the mean aerodynamic chord (0 % leading edge to 100 % trailing edge):
 * the band where the balance point should sit for the Settings static-margin range, the
 * balance point at both payloads and the neutral point.
 */
function BalanceStrip({ e, settings }: { e: Estimates; settings: Settings }) {
  const b = e.balance;
  const g = e.geometry;
  if (!b || !g) return null;
  const mac = g.wing.mac_mm;
  const toPct = (x: number) => ((x - g.wing.mac_x_le_mm) / mac) * 100;
  const np = toPct(b.neutral_point_x.value);
  const heavy = b.cg_max_payload_mac.value;
  const light = b.cg_min_payload_mac.value;
  const bandHi = np - settings.checks.static_margin_min * 100;
  const bandLo = np - settings.checks.static_margin_max * 100;
  const values = [np, heavy, light, bandLo, bandHi].filter(Number.isFinite);
  const lo = Math.min(0, ...values) - 5;
  const hi = Math.max(60, ...values) + 5;
  const W = 300;
  const X = (pct: number) => ((pct - lo) / (hi - lo)) * W;
  if (!values.length) return null;
  return (
    <figure className="balance-strip">
      <svg viewBox={`0 0 ${W} 46`} role="img" aria-label={`Balance: heaviest camera at ${heavy.toFixed(1)} % of the chord, lightest at ${light.toFixed(1)} %, neutral point at ${np.toFixed(1)} %.`}>
        <rect x={X(bandLo)} y={10} width={Math.max(0, X(bandHi) - X(bandLo))} height={16} rx={3} className="strip-band" />
        <line x1={X(0)} x2={X(Math.min(100, hi))} y1={18} y2={18} className="strip-axis" />
        {[0, 25, 50].map((t) => (
          <g key={t}>
            <line x1={X(t)} x2={X(t)} y1={26} y2={30} className="strip-axis" />
            <text x={X(t)} y={41} className="strip-tick" textAnchor="middle">
              {t} %
            </text>
          </g>
        ))}
        <path d={`M ${X(np)} 6 l -6 -6 h 12 z`} transform="translate(0 4)" className="strip-np" />
        <circle cx={X(heavy)} cy={18} r={5} className="strip-cg-heavy" />
        <circle cx={X(light)} cy={18} r={5} className="strip-cg-light" />
      </svg>
      <figcaption className="strip-legend small muted">
        <span>
          <span className="legend-dot legend-dot-filled" aria-hidden="true" /> heaviest camera
        </span>
        <span>
          <span className="legend-dot" aria-hidden="true" /> lightest camera
        </span>
        <span>
          <span className="legend-tri" aria-hidden="true" /> neutral point
        </span>
        <span>
          <span className="legend-band" aria-hidden="true" /> target band
        </span>
      </figcaption>
    </figure>
  );
}

/** Which masses come from the parts chosen on the Parts tab (or that none do yet). */
function PartsMassesNote({ partsMasses }: { partsMasses: PartsMasses | null | undefined }) {
  const [params] = useSearchParams();
  const names = partsMassNames(partsMasses);
  const partsLink = (() => {
    const next = new URLSearchParams(params);
    next.set('tab', 'parts');
    return `?${next.toString()}`;
  })();
  if (names.length === 0) {
    return (
      <p className="small muted parts-masses-note" data-testid="estimates-parts-masses" data-count={0}>
        Motor, propeller, ESC, battery, avionics and tube masses are statistical estimates until a parts list is stored (
        <Link to={partsLink}>Parts tab</Link>).
      </p>
    );
  }
  return (
    <p className="small parts-masses-note" data-testid="estimates-parts-masses" data-count={names.length}>
      <span className="pill pill-info">From selected parts</span> {names.join(', ')} (
      <Link to={partsLink}>Parts tab</Link>). The rest are statistical estimates.
    </p>
  );
}

export function EstimatesPanel({
  estimates,
  settings,
  partsMasses,
}: {
  estimates: Estimates;
  settings: Settings;
  /** Phase 4: the draft's selected parts' masses, to say which masses come from real parts. */
  partsMasses?: PartsMasses | null;
}) {
  const e = estimates;
  if (!e.valid || !e.mass || !e.balance || !e.aero || !e.performance) {
    return (
      <section className="card estimates-panel" data-testid="estimates-panel" aria-labelledby="estimates-heading">
        <div className="card-header">
          <h2 id="estimates-heading">Estimates</h2>
        </div>
        <p className="card-note">The numbers cannot be computed for this design yet:</p>
        <StatusList statuses={e.statuses} />
      </section>
    );
  }
  const { mass, balance, aero, performance: perf, statuses } = e;
  return (
    <section className="card estimates-panel" data-testid="estimates-panel" aria-labelledby="estimates-heading">
      <div className="card-header">
        <h2 id="estimates-heading">Estimates</h2>
        <span className="small faint" title="Time the engine took for this design">
          {e.elapsed_ms.toFixed(1)} ms
        </span>
      </div>
      <p className="card-note">
        Quick Tier 1 estimates, recomputed on every edit. Ranges show the uncertainty; tap <strong>?</strong> for how
        each number is worked out.
      </p>

      <h3 className="metric-group-title">Mass</h3>
      <div className="metric-grid">
        <Metric id="mass" q={mass.takeoff_max_payload} label="Take-off mass" level={worst(statuses, ['check.mtow'])} />
        <Metric id="mass_min" q={mass.takeoff_min_payload} label="With the lightest camera" />
      </div>
      <PartsMassesNote partsMasses={partsMasses} />
      <MassBreakdown components={mass.components} total={mass.takeoff_max_payload.value} />

      <h3 className="metric-group-title">Balance</h3>
      <BalanceStrip e={e} settings={settings} />
      <div className="metric-grid">
        <Metric id="cg_max" q={balance.cg_max_payload_mac} label="Balance point, heaviest camera" />
        <Metric id="cg_min" q={balance.cg_min_payload_mac} label="Balance point, lightest camera" />
        <Metric
          id="static_margin_max"
          q={balance.static_margin_max_payload}
          label="Static margin, heaviest camera"
          level={worst(statuses, ['check.static_margin_max_payload'])}
        />
        <Metric
          id="static_margin_min"
          q={balance.static_margin_min_payload}
          label="Static margin, lightest camera"
          level={worst(statuses, ['check.static_margin_min_payload'])}
        />
      </div>

      <h3 className="metric-group-title">Wing</h3>
      <div className="metric-grid">
        <Metric id="wing_loading" q={aero.wing_loading} />
        <Metric id="stall_speed" q={aero.stall_speed} />
        <Metric
          id="cruise_to_stall"
          q={aero.cruise_to_stall}
          label="Cruise / stall speed"
          level={worst(statuses, ['check.cruise_to_stall'])}
        />
        <Metric id="lift_to_drag" q={aero.lift_to_drag} label="Lift-to-drag (cruise)" digits={1} />
      </div>

      <h3 className="metric-group-title">Power and endurance</h3>
      <div className="metric-grid">
        <Metric id="cruise_power" q={perf.cruise_power} />
        <Metric id="hover_power" q={perf.hover_power} />
        <Metric
          id="endurance"
          q={perf.endurance_cruise}
          label="Wing-flight endurance"
          rangeFirst
          level={worst(statuses, ['check.endurance'])}
        />
        <Metric id="range" q={perf.range} label="Range (still air)" rangeFirst />
      </div>
      <p className="a3-note small" data-testid="a3-note">
        <strong>EU Open A3:</strong> {A3_NOTE}
      </p>

      <h3 className="metric-group-title">Checks</h3>
      <StatusList statuses={statuses} />

      <details className="breakdown">
        <summary>Assumptions</summary>
        <ul className="assumptions small">
          {e.assumptions.map((a, i) => (
            <li key={i}>{a}</li>
          ))}
        </ul>
      </details>
    </section>
  );
}

export function StatusList({ statuses }: { statuses: Status[] }) {
  return (
    <ul className="status-list">
      {statuses.map((s) => (
        <li key={s.key} className={`status-item status-${s.level}`} data-testid={`status-${s.key}`} data-level={s.level}>
          <div className="status-item-head">
            <StatusBadge level={s.level} />
            <span className="status-label">{s.label}</span>
          </div>
          <p className="status-message small">{s.message}</p>
        </li>
      ))}
    </ul>
  );
}
