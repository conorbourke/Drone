/**
 * The body of a finished (or still finding improvements) full analysis: headline numbers with
 * ranges, checks, mission and transition charts, mass and drag tables, structure with the span
 * loading, Tier 1 vs Tier 2 differences, stability derivatives, assumptions and notes.
 */
import type { ReactNode } from 'react';
import type { AnalysisResult, ServerQuantity, SparCheck, Tier1Row } from '../api/analysis';
import { BarChart, LineChart } from '../components/Charts';
import { CheckList } from '../components/CheckList';
import { Explain } from '../components/Explain';
import { formatSig } from '../lib/analysis';
import { A3_NOTE, StatusBadge } from './EstimatesPanel';

function fmt(v: number | null | undefined, digits?: number): string {
  return v === null || v === undefined ? '–' : formatSig(v, digits);
}

function explainServer(q: ServerQuantity): string {
  const unit = q.unit ? ` ${q.unit}` : '';
  const range =
    q.low !== null && q.high !== null && q.value !== null && Math.abs(q.high - q.low) > 1e-9
      ? `Plausible range ${fmt(q.low)} to ${fmt(q.high)}${unit}; best estimate ${fmt(q.value)}${unit}.`
      : '';
  return [q.explain, range, q.source ? `Method: ${q.source}` : ''].filter(Boolean).join('\n\n');
}

/** One headline number: label, Explain, value, range. */
export function ServerMetric({
  id,
  q,
  label,
  digits,
  rangeFirst,
  extra,
}: {
  id: string;
  q: ServerQuantity | undefined;
  label?: string;
  digits?: number;
  rangeFirst?: boolean;
  extra?: ReactNode;
}) {
  if (!q) return null;
  const name = label ?? q.label;
  const unit = q.unit ? ` ${q.unit}` : '';
  const hasRange = q.low !== null && q.high !== null && Math.abs(q.high - q.low) > 1e-9;
  return (
    <div className="metric" data-testid={`analysis-metric-${id}`} data-value={q.value ?? ''}>
      <div className="metric-head">
        <span className="metric-label">{name}</span>
        <Explain label={name} text={explainServer(q)} />
      </div>
      <div className="metric-value">
        {rangeFirst && hasRange ? `${fmt(q.low, digits)}–${fmt(q.high, digits)}${unit}` : `${fmt(q.value, digits)}${unit}`}
      </div>
      {hasRange ? (
        <div className="metric-sub">
          {rangeFirst ? `best estimate ${fmt(q.value, digits)}${unit}` : `range ${fmt(q.low, digits)}–${fmt(q.high, digits)}`}
        </div>
      ) : null}
      {extra}
    </div>
  );
}

const HEADLINE: Array<{ key: string; label?: string; rangeFirst?: boolean; digits?: number }> = [
  { key: 'takeoff_mass', label: 'Take-off mass' },
  { key: 'endurance_cruise', label: 'Wing-flight endurance', rangeFirst: true, digits: 0 },
  { key: 'range', label: 'Range (still air)', rangeFirst: true, digits: 1 },
  { key: 'cruise_power', label: 'Cruise power', digits: 0 },
  { key: 'hover_power', label: 'Hover power', digits: 0 },
  { key: 'stall_speed', label: 'Stall speed', digits: 1 },
  { key: 'lift_to_drag', label: 'Lift-to-drag', digits: 1 },
  { key: 'static_margin_max_payload', label: 'Static margin, heaviest camera', digits: 1 },
  { key: 'static_margin_min_payload', label: 'Static margin, lightest camera', digits: 1 },
  { key: 'hover_thrust_to_weight', label: 'Hover thrust-to-weight', digits: 2 },
  { key: 'transition_min_margin', label: 'Transition thrust margin', digits: 2 },
  { key: 'peak_current', label: 'Peak battery current', digits: 1 },
];

const DERIVATIVES: Record<string, { name: string; explain: string }> = {
  CL_alpha: {
    name: 'Lift slope CL_α (/rad)',
    explain: 'How much lift grows per radian of angle of attack. Higher means the aircraft reacts more strongly to gusts.',
  },
  Cm_alpha: {
    name: 'Pitch stiffness Cm_α (/rad)',
    explain:
      'Negative means the nose comes back down by itself after a gust lifts it: statically stable in pitch. The more negative, the stiffer.',
  },
  Cm_q: {
    name: 'Pitch damping Cm_q (/rad)',
    explain: 'Negative means pitching motions die out instead of oscillating. Mostly from the tail.',
  },
  Cm_elevator_per_deg: {
    name: 'Elevator power Cm_δe (/°)',
    explain: 'Pitching moment from one degree of elevator. Sets how much elevator the aircraft needs to trim.',
  },
  Cn_beta: {
    name: 'Weathercock stability Cn_β (/rad)',
    explain:
      'Positive means the nose turns back into the wind after a sideslip: stable in yaw. From the vertical tail (or the V-tail).',
  },
  Cl_beta: {
    name: 'Dihedral effect Cl_β (/rad)',
    explain: 'Negative means a sideslip rolls the aircraft level again: stable in roll. From dihedral and the wing position.',
  },
  CY_beta: {
    name: 'Side force CY_β (/rad)',
    explain: 'Side force when the aircraft flies slightly sideways. Normally negative.',
  },
  Cl_p: {
    name: 'Roll damping Cl_p (/rad)',
    explain: 'Negative means a roll slows down by itself when the controls are centred.',
  },
  Cn_r: { name: 'Yaw damping Cn_r (/rad)', explain: 'Negative means yawing motions die out.' },
};

function Section({
  title,
  children,
  testId,
  collapsed,
  note,
}: {
  title: string;
  children: ReactNode;
  testId?: string;
  collapsed?: boolean;
  note?: string;
}) {
  if (collapsed) {
    return (
      <details className="analysis-section analysis-collapsible" data-testid={testId}>
        <summary>
          <h3>{title}</h3>
        </summary>
        {note ? <p className="card-note">{note}</p> : null}
        {children}
      </details>
    );
  }
  return (
    <section className="analysis-section" data-testid={testId}>
      <h3>{title}</h3>
      {note ? <p className="card-note">{note}</p> : null}
      {children}
    </section>
  );
}

function StructureCard({ title, check, kind }: { title: string; check: SparCheck | undefined; kind: 'spar' | 'boom' }) {
  if (!check) return null;
  return (
    <div className="structure-card" data-testid={`analysis-structure-${kind}`} data-level={check.level}>
      <div className="status-item-head">
        <StatusBadge level={check.level} />
        <span className="status-label">{title}</span>
        <Explain
          label={title}
          text={[
            kind === 'spar'
              ? `Root bending of the wing spar at ${fmt(check.load_factor)} g × safety factor ${fmt(check.safety_factor)}, using the span loading below.`
              : `Boom bending with full motor thrust at the motor (and a landing load); the ${check.critical_case ?? 'worst'} case decides.`,
            'Margin = allowable stress / actual stress − 1: above 0 the part holds at the design load.',
            check.source ? `Source: ${check.source}` : '',
          ]
            .filter(Boolean)
            .join('\n\n')}
        />
      </div>
      <dl className="kv kv-compact">
        <dt>Tube</dt>
        <dd>{check.spar ?? check.tube ?? '–'}</dd>
        <dt>Margin</dt>
        <dd>
          <strong>
            {check.margin >= 0 ? '+' : ''}
            {fmt(check.margin * 100, 0)} %
          </strong>
        </dd>
        <dt>Stress / allowable</dt>
        <dd>
          {fmt(check.stress_mpa, 0)} / {fmt(check.allowable_mpa, 0)} MPa
        </dd>
        {kind === 'spar' ? (
          <>
            <dt>Tip deflection at 1 g</dt>
            <dd>
              {fmt(check.tip_deflection_1g_mm, 0)} mm (limit {fmt(check.tip_deflection_limit_mm, 0)} mm at the design load)
            </dd>
          </>
        ) : (
          <>
            <dt>Tip deflection, full thrust</dt>
            <dd>{fmt(check.tip_deflection_full_thrust_mm, 1)} mm</dd>
          </>
        )}
      </dl>
    </div>
  );
}

function Tier1Table({ rows }: { rows: Tier1Row[] }) {
  return (
    <div className="table-scroll">
      <table className="table table-compact tier-table" data-testid="analysis-tier-compare">
        <thead>
          <tr>
            <th scope="col">Number</th>
            <th scope="col" className="num">
              Tier 1 (browser)
            </th>
            <th scope="col" className="num">
              Tier 2 (analysis)
            </th>
            <th scope="col" className="num">
              Difference
            </th>
            <th scope="col">Why they differ</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const big = r.difference_pct !== null && Math.abs(r.difference_pct) >= 10;
            const unit = r.unit ? ` ${r.unit}` : '';
            return (
              <tr key={r.key} data-row={r.key} className={big ? 'is-diff' : undefined}>
                <th scope="row">{r.label}</th>
                <td className="num">
                  {fmt(r.tier1)}
                  {unit}
                </td>
                <td className="num">
                  {fmt(r.tier2)}
                  {unit}
                </td>
                <td className={`num diff-cell${big ? ' is-big' : ''}`}>
                  {r.difference_pct === null
                    ? '–'
                    : `${r.difference_pct >= 0 ? '+' : '−'}${fmt(Math.abs(r.difference_pct), 0)} %`}
                </td>
                <td className="why-cell small">{r.why}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** `afterChecks` (the recommendations) is shown right after the checks, before the details. */
export function AnalysisResultView({ result, afterChecks }: { result: AnalysisResult; afterChecks?: ReactNode }) {
  const s = result.summary ?? {};
  if (!result.valid) {
    return (
      <div className="stack-sm">
        <p className="card-note">The analysis could not run on this design:</p>
        <CheckList checks={result.checks ?? []} />
      </div>
    );
  }
  const segments = result.mission?.payload_max?.segments ?? [];
  const tr = result.transition;
  const spar = result.structure?.wing_spar;
  const boom = result.structure?.boom;
  const derivatives = result.aero?.stability_derivatives ?? {};
  const counts = { ok: 0, warn: 0, fail: 0 };
  for (const c of result.checks ?? []) if (c.level in counts) counts[c.level as keyof typeof counts] += 1;

  return (
    <div className="analysis-body" data-testid="analysis-results">
      <div className="metric-grid analysis-metrics">
        {HEADLINE.map((h) => (
          <ServerMetric key={h.key} id={h.key} q={s[h.key]} label={h.label} rangeFirst={h.rangeFirst} digits={h.digits} />
        ))}
      </div>
      <p className="a3-note small" data-testid="analysis-a3-note">
        <strong>EU Open A3 (range and endurance):</strong> {result.a3_note ?? A3_NOTE}
      </p>

      <Section
        title="Checks"
        testId="analysis-checks-section"
        note={`${counts.ok} pass, ${counts.warn} warning${counts.warn === 1 ? '' : 's'}, ${counts.fail} fail.`}
      >
        <CheckList checks={result.checks ?? []} />
      </Section>

      {afterChecks}

      <div className="analysis-chart-grid">
        {segments.length ? (
          <Section
            title="Mission profile"
            testId="analysis-mission"
            note="Heaviest camera: power drawn from the battery and energy used in each phase."
          >
            <h4 className="chart-title">Battery power by phase</h4>
            <BarChart
              testId="chart-mission-power"
              ariaLabel="Battery power by mission phase"
              unit="W"
              valueLabel="Power"
              format={(v) => fmt(v, 0)}
              bars={segments.map((g) => ({
                key: g.key,
                label: g.label,
                value: g.power_w,
                note: `${fmt(g.duration_s / 60, 1)} min`,
              }))}
            />
            <h4 className="chart-title">Energy by phase</h4>
            <BarChart
              testId="chart-mission-energy"
              ariaLabel="Energy used by mission phase"
              unit="Wh"
              valueLabel="Energy"
              format={(v) => fmt(v, 1)}
              bars={segments.map((g) => ({
                key: g.key,
                label: g.label,
                value: g.energy_wh,
                note: `${fmt(g.duration_s / 60, 1)} min`,
              }))}
            />
            {result.mission?.payload_max ? (
              <p className="small muted">
                Usable energy {fmt(result.mission.payload_max.usable_wh, 0)} Wh, of which take-off, transitions and landing use{' '}
                {fmt(result.mission.payload_max.vtol_wh, 1)} Wh.
              </p>
            ) : null}
          </Section>
        ) : null}

        {tr && tr.points?.length ? (
          <Section
            title="Transition"
            testId="analysis-transition"
            note={`From hover to cruise speed. Lowest thrust margin ${fmt(tr.min_thrust_margin, 2)} at ${fmt(tr.min_margin_speed_mps, 1)} m/s; peak power ${fmt(tr.peak_power_w, 0)} W (${fmt(tr.peak_current_a, 1)} A).${tr.speed_wing_80pct_mps ? ` The wing carries 80 % of the weight from ${fmt(tr.speed_wing_80pct_mps, 1)} m/s.` : ''}`}
          >
            <h4 className="chart-title">
              Thrust margin{' '}
              <Explain
                label="thrust margin"
                text="Thrust the motors can give at full throttle divided by the thrust needed at that speed. Below 1.0 the aircraft cannot hold height; the minimum from Settings leaves room for gusts and control."
              />
            </h4>
            <LineChart
              testId="chart-transition-margin"
              ariaLabel="Thrust margin against airspeed through the transition"
              points={tr.points.map((p) => ({ x: p.speed_mps, y: p.thrust_margin }))}
              xLabel="Airspeed"
              xUnit="m/s"
              yLabel="Thrust margin"
              yUnit="×"
              formatY={(y) => fmt(y, 2)}
              formatX={(x) => fmt(x, 1)}
              markX={tr.min_margin_speed_mps}
              references={[
                { y: 1, label: '1.0: cannot hold height', tone: 'critical' },
                ...(tr.margin_min_setting
                  ? [
                      {
                        y: tr.margin_min_setting,
                        label: `minimum ${fmt(tr.margin_min_setting, 2)} (Settings)`,
                        tone: 'warning' as const,
                      },
                    ]
                  : []),
              ]}
            />
            <h4 className="chart-title">Battery power</h4>
            <LineChart
              testId="chart-transition-power"
              ariaLabel="Battery power against airspeed through the transition"
              points={tr.points.map((p) => ({ x: p.speed_mps, y: p.power_w }))}
              xLabel="Airspeed"
              xUnit="m/s"
              yLabel="Power"
              yUnit="W"
              zeroBased
              formatY={(y) => fmt(y, 0)}
              formatX={(x) => fmt(x, 1)}
            />
          </Section>
        ) : null}
      </div>

      {spar || boom ? (
        <Section title="Structure" testId="analysis-structure">
          <div className="structure-grid">
            <StructureCard title="Wing spar" check={spar} kind="spar" />
            <StructureCard title="Motor booms" check={boom} kind="boom" />
          </div>
          {spar?.span_loading?.length ? (
            <>
              <h4 className="chart-title">
                Span loading{' '}
                <Explain
                  label="span loading"
                  text="Lift per metre of span along one wing half at the cruise point (AVL). Lift near the root bends the spar least; lift near the tip bends it most."
                />
              </h4>
              <LineChart
                testId="chart-span-loading"
                ariaLabel="Lift per metre along the half span"
                points={spar.span_loading.map((p) => ({ x: p.y_mm, y: p.lift_n_per_m }))}
                xLabel="Distance from the centreline"
                xUnit="mm"
                yLabel="Lift"
                yUnit="N/m"
                zeroBased
                formatY={(y) => fmt(y, 1)}
                formatX={(x) => fmt(x, 0)}
              />
            </>
          ) : null}
        </Section>
      ) : null}

      {result.tier1_comparison?.length ? (
        <Section
          title="Tier 1 vs Tier 2"
          testId="analysis-tier-section"
          note="The instant browser estimates (Tier 1) against this analysis (Tier 2). Rows that differ by 10 % or more are highlighted; the last column says why. Use Tier 2 for decisions."
        >
          <Tier1Table rows={result.tier1_comparison} />
        </Section>
      ) : null}

      {result.mass?.components?.length ? (
        <Section
          title="Mass table"
          testId="analysis-mass"
          collapsed
          note="Heaviest camera; same build-up as the browser, with the spar tube sized by the structure check."
        >
          <div className="table-scroll">
            <table className="table table-compact" data-testid="analysis-mass-table">
              <thead>
                <tr>
                  <th>Item</th>
                  <th className="num">Mass</th>
                  <th className="num">x from nose</th>
                  <th className="num">±</th>
                  <th aria-label="Explanation" />
                </tr>
              </thead>
              <tbody>
                {[...result.mass.components]
                  .sort((a, b) => b.mass_g - a.mass_g)
                  .map((c) => (
                    <tr key={c.key}>
                      <td>{c.label}</td>
                      <td className="num">{fmt(c.mass_g, 0)} g</td>
                      <td className="num">{fmt(c.x_mm, 0)} mm</td>
                      <td className="num">{fmt(c.uncertainty * 100, 0)} %</td>
                      <td>
                        <Explain label={c.label} text={[c.explain, `Source: ${c.source}`].join('\n\n')} />
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}

      {result.drag?.items?.length ? (
        <Section
          title="Drag table"
          testId="analysis-drag"
          collapsed
          note={`At cruise with the heaviest camera. Total drag coefficient ${fmt(result.drag.cd_total_cruise, 4)} (profile ${fmt(result.drag.cd_profile, 4)}, parasite ${fmt(result.drag.cd_parasite, 4)}, induced ${fmt(result.drag.cd_induced, 4)}).`}
        >
          <div className="table-scroll">
            <table className="table table-compact" data-testid="analysis-drag-table">
              <thead>
                <tr>
                  <th>Item</th>
                  <th className="num">CD</th>
                  <th className="num">Drag area</th>
                  <th className="num">Share</th>
                  <th aria-label="Explanation" />
                </tr>
              </thead>
              <tbody>
                {[...result.drag.items]
                  .sort((a, b) => b.share - a.share)
                  .map((d) => (
                    <tr key={d.key}>
                      <td>{d.label}</td>
                      <td className="num">{fmt(d.cd, 4)}</td>
                      <td className="num">{fmt(d.drag_area_m2 * 1e4, 1)} cm²</td>
                      <td className="num">{fmt(d.share * 100, 0)} %</td>
                      <td>
                        <Explain label={d.label} text={[d.explain, `Source: ${d.source}`].join('\n\n')} />
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}

      {Object.keys(derivatives).length ? (
        <Section
          title="Stability derivatives"
          testId="analysis-stability"
          collapsed
          note={typeof derivatives.source === 'string' ? derivatives.source : undefined}
        >
          <dl className="derivative-list">
            {Object.entries(DERIVATIVES)
              .filter(([key]) => typeof derivatives[key] === 'number')
              .map(([key, d]) => (
                <div key={key} className="derivative-row" data-testid={`derivative-${key}`}>
                  <dt>
                    {d.name}: <strong>{fmt(derivatives[key] as number, 3)}</strong>
                  </dt>
                  <dd className="small muted">{d.explain}</dd>
                </div>
              ))}
          </dl>
        </Section>
      ) : null}

      {result.notes?.length ? (
        <Section title="Notes" testId="analysis-notes">
          <ul className="assumptions small">
            {result.notes.map((n) => (
              <li key={n.key}>
                <strong>{n.label}:</strong> {n.message}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {result.assumptions?.length ? (
        <Section title="Assumptions" testId="analysis-assumptions" collapsed>
          <ul className="assumptions small">
            {result.assumptions.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </Section>
      ) : null}
    </div>
  );
}
