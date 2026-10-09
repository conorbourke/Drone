/**
 * Full-scale checks (Phase 7, docs/phases/PHASE7.md section 2): the Phase 3 checks and the
 * 24 kg checks of the chosen design, then the details behind them in plain words: the mass
 * thresholds, motor-out hover (every case, the worst one and the octocopter recommendation),
 * the spar and boom tubes the structure needs, the composite layup against the mass model, the
 * landing gear load, the battery current with the Li-ion alternatives, and the range and
 * endurance with the A3 operating note.
 */
import { useState } from 'react';
import type { ReactNode } from 'react';
import type { ExportSource } from '../../api/exports';
import {
  runFullscale,
  type BatteryResult,
  type FullscaleResult,
  type LandingGearResult,
  type LayupResult,
  type MotorOutResult,
  type MtowResult,
  type StructureResult,
  type TubeRow,
} from '../../api/fullscale';
import { errorMessage, isAuthError } from '../../api/client';
import { CheckList } from '../../components/CheckList';
import { Explain } from '../../components/Explain';
import { StatusPill } from '../../components/StatusPill';
import { quantityShort } from '../../lib/analysis';
import { formatDateTime, formatMassG, formatMassKg, formatNumber, formatWithUnit, pluralize } from '../../lib/format';
import { motorOutVerdict, percentText, plyOrientationText, utilisationText } from '../../lib/moulds';
import { StatusBadge } from '../../panels/EstimatesPanel';

function Figure({ label, explain, value, testId }: { label: string; explain: string; value: string; testId?: string }) {
  return (
    <div>
      <dt>
        {label} <Explain label={label.toLowerCase()} text={explain} />
      </dt>
      <dd data-testid={testId}>{value}</dd>
    </div>
  );
}

function Section({ title, level, children, testId }: { title: string; level?: string; children: ReactNode; testId: string }) {
  return (
    <section className="fs-section" data-testid={testId} data-level={level}>
      <h3 className="files-section-title">
        <span>{title}</span>
        {level === 'ok' || level === 'warn' || level === 'fail' || level === 'info' ? <StatusBadge level={level} /> : null}
      </h3>
      {children}
    </section>
  );
}

function MtowBlock({ mtow, layup }: { mtow: MtowResult; layup?: MtowResult }) {
  return (
    <Section title="Take-off mass" level={layup && layup.level === 'fail' ? 'fail' : mtow.level} testId="fullscale-mtow">
      <p className="small">{mtow.banner}</p>
      {layup ? <p className="small">{layup.banner}</p> : null}
      <dl className="files-figures">
        <Figure
          label="Mass model"
          explain="Take-off mass with the heaviest camera from the analysis mass model, which uses areal densities for the carbon structure."
          value={formatMassKg(mtow.mass_kg)}
          testId="fullscale-mass"
        />
        {layup ? (
          <Figure
            label="With the layup"
            explain="The same take-off mass with the structure weighed ply by ply from the composite layup suggestion below instead of the areal densities."
            value={formatMassKg(layup.mass_kg)}
          />
        ) : null}
        {mtow.thresholds.map((t) => (
          <Figure
            key={t.key}
            label={t.label}
            explain={`From Settings (${t.setting}). ${t.exceeded ? 'The design is at or above it.' : `The design is ${formatMassKg(t.margin_kg)} below it.`}`}
            value={formatMassKg(t.kg)}
          />
        ))}
      </dl>
    </Section>
  );
}

function MotorOut({ mo }: { mo: MotorOutResult }) {
  const worst = mo.worst_case;
  const failing = mo.cases.filter((c) => c.level === 'fail').length;
  const rec = mo.recommendation;
  return (
    <Section title="Motor-out hover" level={mo.level} testId="fullscale-motor-out">
      <p className="small">
        With one of the four lift motors failed, can the other three still hold the aircraft level, keep its heading and let it come down
        under control? Each motor is failed in turn, with the lightest and the heaviest camera ({pluralize(mo.cases.length, 'case')}).{' '}
        <Explain label="motor-out model" text={mo.model.join(' ')} />
      </p>
      <div className={`fs-callout fs-callout-${worst.level}`} data-testid="fullscale-motor-out-worst">
        <div className="fs-callout-title">
          Worst case: {worst.failed_motor} out, {worst.payload_case}
        </div>
        <p className="small">
          <strong>{motorOutVerdict(worst)}.</strong> {worst.message}
        </p>
        <p className="small muted">
          {failing === 0 ? 'Every case passes.' : `${failing} of ${mo.cases.length} cases fail.`} Each motor gives at most{' '}
          {formatWithUnit(mo.max_static_thrust_per_motor_n, 'N', { maxFractionDigits: 0 })} at full throttle (about{' '}
          {formatMassKg(mo.max_static_thrust_per_motor_n / 9.81)} of lift).
        </p>
      </div>
      <div className="fs-table-wrap">
        <table className="table fs-table" data-testid="fullscale-motor-out-cases">
          <thead>
            <tr>
              <th scope="col">Failed motor</th>
              <th scope="col">Camera</th>
              <th scope="col">Result</th>
              <th scope="col" className="num">
                Busiest motor <Explain label="busiest motor" text="How hard the hardest-working remaining motor has to run, as a share of its full thrust. Above 100 % is impossible." />
              </th>
              <th scope="col" className="num">
                Roll left <Explain label="roll authority" text="How much rolling moment is still available, as a share of what the intact aircraft has. At least 25 % is wanted to fight a gust." />
              </th>
              <th scope="col" className="num">
                Yaw left <Explain label="yaw authority" text="How much turning moment is still available to hold the heading, as a share of the intact aircraft's. At least 10 % is wanted." />
              </th>
              <th scope="col" className="num">
                Battery <Explain label="battery current" text="Current drawn from the battery in this case, amperes." />
              </th>
            </tr>
          </thead>
          <tbody>
            {mo.cases.map((c, i) => (
              <tr key={i} data-testid="fullscale-motor-out-case" data-level={c.level}>
                <td data-label="Failed motor">{c.failed_motor}</td>
                <td data-label="Camera">{c.payload_case}</td>
                <td data-label="Result">
                  <StatusBadge level={c.level} /> <span className="small">{motorOutVerdict(c)}</span>
                </td>
                <td data-label="Busiest motor" className="num">
                  {utilisationText(c.max_utilisation)}
                </td>
                <td data-label="Roll left" className="num">
                  {c.roll_authority_fraction !== null && c.roll_authority_fraction < 0 ? 'none' : percentText(c.roll_authority_fraction)}
                </td>
                <td data-label="Yaw left" className="num">
                  {percentText(c.yaw_authority_fraction)}
                </td>
                <td data-label="Battery" className="num">
                  {c.battery_current_a === null ? '—' : formatWithUnit(c.battery_current_a, 'A', { maxFractionDigits: 0 })}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rec.needed ? (
        <div className="fs-callout fs-callout-info" data-testid="fullscale-x8">
          <div className="fs-callout-title">Recommendation: {rec.layout ?? 'an octocopter'}</div>
          <p className="small">{rec.message}</p>
          {rec.x8 ? (
            <dl className="files-figures">
              <Figure
                label="Thrust needed per motor"
                explain="Static thrust each of the eight motors must give so that, with any one failed, its coaxial partner and the other pairs still hold the aircraft (the lower rotors work in the upper rotors' wake and give less)."
                value={formatWithUnit(rec.x8.required_static_thrust_per_motor_n, 'N', { maxFractionDigits: 0 })}
              />
              <Figure
                label="Present motors give"
                explain="Full-throttle static thrust of the motors in the design now."
                value={formatWithUnit(rec.x8.current_motor_static_thrust_n, 'N', { maxFractionDigits: 0 })}
              />
              <Figure
                label="Same motors enough?"
                explain="Whether eight of the present motors would meet the thrust needed."
                value={rec.x8.same_motors_enough ? 'Yes' : 'No, larger motors needed'}
              />
            </dl>
          ) : null}
        </div>
      ) : (
        <p className="small" data-testid="fullscale-x8">
          No change of layout is needed: the quad holds attitude and heading with any one motor out.
        </p>
      )}
      <p className="small faint">Source: {mo.source}</p>
    </Section>
  );
}

function bestTube(rows: TubeRow[]): TubeRow | null {
  const ok = rows.filter((r) => r.ok);
  if (ok.length) return [...ok].sort((a, b) => (a.mass_per_m_g ?? 1e9) - (b.mass_per_m_g ?? 1e9))[0];
  return null;
}

function TubeTable({ rows, testId }: { rows: TubeRow[]; testId: string }) {
  if (rows.length === 0) return <p className="small muted">The parts catalogue has no carbon tubes to compare.</p>;
  return (
    <details className="breakdown">
      <summary>Catalogue tubes compared ({rows.length})</summary>
      <ul className="fs-tube-list" data-testid={testId}>
        {rows.map((r, i) => (
          <li key={i} className="fs-tube">
            <div>
              <StatusPill tone={r.ok ? 'ok' : 'error'}>{r.ok ? '✓ Enough' : 'Not enough'}</StatusPill> <strong>{r.part}</strong>
            </div>
            <div className="small muted">
              {formatNumber(r.outer_mm, { maxFractionDigits: 1 })} × {formatNumber(r.inner_mm, { maxFractionDigits: 1 })}&#8239;mm
              {r.mass_per_m_g !== null ? `, ${formatNumber(r.mass_per_m_g, { maxFractionDigits: 0 })} g/m` : ''}
              {r.stress_mpa !== null ? `, ${formatNumber(r.stress_mpa, { maxFractionDigits: 0 })} MPa` : ''}
              {r.margin !== null ? `, margin ${r.margin >= 0 ? '+' : ''}${formatNumber(r.margin, { maxFractionDigits: 2 })}` : ''}
              {r.problems.length ? `. ${r.problems.join('; ')}` : ''}
            </div>
          </li>
        ))}
      </ul>
    </details>
  );
}

function Structure({ s }: { s: StructureResult }) {
  const spar = s.spar;
  const booms = s.booms;
  const sparBest = bestTube(spar.catalogue);
  const boomBest = bestTube(booms.catalogue);
  const worst = spar.level === 'fail' || booms.level === 'fail' ? 'fail' : spar.level === 'warn' || booms.level === 'warn' ? 'warn' : 'ok';
  return (
    <Section title="Spar and boom tubes" level={worst} testId="fullscale-structure">
      <div className="fs-two">
        <div data-testid="fullscale-spar">
          <h4 className="fs-sub">
            Wing spar <StatusBadge level={spar.level} />
          </h4>
          <p className="small">{spar.message}</p>
          <dl className="files-figures">
            <Figure
              label="Root bending moment"
              explain="Bending moment at the wing root at the manoeuvre load factor times the safety factor (from Settings): what the spar must carry without breaking."
              value={formatWithUnit(spar.root_moment_ultimate_nm, 'N·m', { maxFractionDigits: 0 })}
            />
            <Figure
              label="Tube that would do"
              explain="Smallest standard carbon tube (outer diameter × wall) with at least +0.25 margin, whether or not it is in the catalogue."
              value={
                spar.needed_standard_tube
                  ? `${formatNumber(spar.needed_standard_tube.outer_mm)} × ${formatNumber(spar.needed_standard_tube.wall_mm)} mm wall`
                  : '—'
              }
            />
            <Figure label="Best catalogue tube" explain="The lightest catalogue tube that passes, if any." value={sparBest ? sparBest.part : 'None'} />
          </dl>
          <TubeTable rows={spar.catalogue} testId="fullscale-spar-tubes" />
        </div>
        <div data-testid="fullscale-booms">
          <h4 className="fs-sub">
            Booms <StatusBadge level={booms.level} />
          </h4>
          <p className="small">{booms.message}</p>
          <dl className="files-figures">
            <Figure
              label="Boom moment"
              explain="Largest bending moment at the boom clamp (full motor thrust or the landing case) times the safety factor."
              value={formatWithUnit(booms.moment_ultimate_nm, 'N·m', { maxFractionDigits: 0 })}
            />
            <Figure
              label="Tube that would do"
              explain="Smallest standard tube that is strong enough and bends less than 1 % of the arm at the motor under full thrust."
              value={booms.needed_outer_mm ? `${formatNumber(booms.needed_outer_mm.outer_mm)} × ${formatNumber(booms.needed_outer_mm.wall_mm)} mm wall` : '—'}
            />
            <Figure label="Best catalogue tube" explain="The lightest catalogue tube that passes, if any." value={boomBest ? boomBest.part : 'None'} />
          </dl>
          <TubeTable rows={booms.catalogue} testId="fullscale-boom-tubes" />
        </div>
      </div>
    </Section>
  );
}

function Layup({ layup }: { layup: LayupResult }) {
  const m = layup.structural_mass;
  return (
    <Section title="Composite layup and mass" testId="fullscale-layup">
      <p className="small">
        A suggested carbon layup for each moulded part (plies of fabric by weight and direction, foam core where used), weighed ply by ply
        and compared with the mass model the analysis uses.
      </p>
      <dl className="files-figures">
        <Figure label="Layup structure" explain="Sum of the cured masses of every part below." value={formatMassG(m.layup_total_g)} testId="fullscale-layup-total" />
        <Figure
          label="Mass model"
          explain="The same parts in the analysis mass model, from areal densities per square metre."
          value={formatMassG(m.mass_model_g)}
        />
        <Figure
          label="Difference"
          explain="Layup minus mass model. A positive number means the real structure is likely heavier than the analysis assumes."
          value={`${m.difference_g >= 0 ? '+' : '−'}${formatMassG(Math.abs(m.difference_g))}`}
        />
        <Figure
          label="Take-off with layup"
          explain="Take-off mass (heaviest camera) with the layup structure in place of the mass model's."
          value={formatMassKg(m.takeoff_mass_with_layup_kg)}
        />
      </dl>
      <ul className="fs-layup-list">
        {layup.parts.map((p) => (
          <li key={p.key} className="fs-layup" data-testid="fullscale-layup-part">
            <div className="fs-layup-head">
              <strong>{p.label}</strong>
              <span className="small">{formatMassG(p.mass_g)}</span>
            </div>
            <ul className="small fs-plies">
              {p.plies.map((ply, i) => (
                <li key={i}>
                  {ply.count} × {ply.fabric}, {plyOrientationText(ply.orientation)} ({ply.position})
                </li>
              ))}
              {p.core ? (
                <li>
                  Core: {p.core.material}, {formatNumber(p.core.thickness_mm, { maxFractionDigits: 1 })}&#8239;mm
                </li>
              ) : null}
            </ul>
            {p.local_reinforcement ? <p className="small muted">{p.local_reinforcement}</p> : null}
          </li>
        ))}
      </ul>
      <details className="breakdown">
        <summary>Sources</summary>
        <ul className="small">
          {layup.sources.map((s, i) => (
            <li key={i}>{s}</li>
          ))}
        </ul>
        {m.note ? <p className="small muted">{m.note}</p> : null}
      </details>
    </Section>
  );
}

function Gear({ gear }: { gear: LandingGearResult }) {
  return (
    <Section title="Landing gear" level={gear.level} testId="fullscale-gear">
      <p className="small">{gear.message}</p>
      <dl className="files-figures">
        <Figure
          label="Landing load"
          explain={`How many times its own weight the aircraft feels on a hard touchdown: ${formatNumber(gear.sink_rate_mps, { maxFractionDigits: 1 })} m/s sink on ${formatNumber(gear.stroke_mm)} mm of gear travel.`}
          value={`${formatNumber(gear.load_factor, { maxFractionDigits: 1 })} g`}
        />
        <Figure
          label="Per attachment"
          explain={`Force at each of the ${gear.attachments} gear attachments in that landing.`}
          value={formatWithUnit(gear.load_per_attachment_n, 'N', { maxFractionDigits: 0 })}
        />
      </dl>
      <p className="small faint">Source: {gear.source}</p>
    </Section>
  );
}

function Battery({ b }: { b: BatteryResult }) {
  return (
    <Section title="Battery current" level={b.level} testId="fullscale-battery">
      <p className="small">{b.message}</p>
      <dl className="files-figures">
        <Figure label="Hover" explain="Current from the pack while hovering at full mass." value={formatWithUnit(b.hover_current_a, 'A', { maxFractionDigits: 0 })} />
        <Figure label="Peak" explain="Highest current in the mission (take-off, transitions)." value={formatWithUnit(b.peak_current_a, 'A', { maxFractionDigits: 0 })} />
        {b.motor_out_current_a !== null ? (
          <Figure
            label="One motor out"
            explain="Current while hovering on the remaining motors in the worst case that can still be flown."
            value={formatWithUnit(b.motor_out_current_a, 'A', { maxFractionDigits: 0 })}
          />
        ) : null}
        <Figure
          label="Allowed"
          explain={`${percentText(b.max_fraction_of_rating)} of the pack's continuous rating (${formatWithUnit(b.continuous_rating_a, 'A', { maxFractionDigits: 0 })}), from Settings.`}
          value={formatWithUnit(b.limit_a, 'A', { maxFractionDigits: 0 })}
        />
      </dl>
      {b.li_ion_alternatives.length ? (
        <>
          <h4 className="fs-sub">Li-ion packs of the same energy</h4>
          <ul className="fs-tube-list" data-testid="fullscale-liion">
            {b.li_ion_alternatives.map((a, i) => (
              <li key={i} className="fs-tube">
                <div>
                  <StatusPill tone={a.ok ? 'ok' : 'error'}>{a.ok ? '✓ Current OK' : 'Too much current'}</StatusPill> <strong>{a.cell}</strong>{' '}
                  {a.config}
                </div>
                <div className="small muted">
                  {formatWithUnit(a.energy_wh, 'Wh', { maxFractionDigits: 0 })}, {formatMassKg(a.mass_kg)}, rated{' '}
                  {formatWithUnit(a.continuous_rating_a, 'A', { maxFractionDigits: 0 })}; the peak uses {percentText(a.peak_fraction_of_rating)} of it
                </div>
              </li>
            ))}
          </ul>
          {b.li_ion_note ? <p className="small muted">{b.li_ion_note}</p> : null}
        </>
      ) : null}
    </Section>
  );
}

function RangeEndurance({ re }: { re: NonNullable<FullscaleResult['range_endurance']> }) {
  return (
    <Section title="Range and endurance" testId="fullscale-range">
      <dl className="files-figures">
        <Figure label={re.endurance_cruise.label} explain={re.endurance_cruise.explain} value={quantityShort(re.endurance_cruise, 0)} />
        <Figure label={re.endurance_total.label} explain={re.endurance_total.explain} value={quantityShort(re.endurance_total, 0)} />
        <Figure label={re.range.label} explain={re.range.explain} value={quantityShort(re.range, 0)} />
      </dl>
      <p className="small" data-testid="fullscale-a3">
        <strong>Operating note (EU Open A3):</strong> {re.a3_note}
      </p>
    </Section>
  );
}

export function FullScaleChecks({ projectId, source, sourceText, flushDraft }: { projectId: number; source: ExportSource; sourceText: string; flushDraft: () => Promise<unknown> }) {
  const [result, setResult] = useState<FullscaleResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setRunning(true);
    setError(null);
    try {
      if (source === 'draft') await flushDraft();
      setResult(await runFullscale(projectId, source));
    } catch (caught) {
      if (!isAuthError(caught)) setError(errorMessage(caught));
    } finally {
      setRunning(false);
    }
  };

  return (
    <section className="card" data-testid="fullscale-card" aria-labelledby="fullscale-heading">
      <div className="card-header">
        <h2 id="fullscale-heading">Full-scale checks</h2>
        <span className="spacer" />
        <button type="button" className="button button-primary" data-testid="fullscale-run" disabled={running} onClick={() => void run()}>
          {running ? 'Checking…' : result ? 'Check again' : 'Run full-scale checks'}
        </button>
      </div>
      <p className="card-note">
        Everything the Design tab checks, plus what matters at the final carbon scale of up to 24&#8239;kg: the mass limits, flying on with one
        motor failed, the spar and boom tubes, the landing load, the battery current and a composite layup. It takes a few seconds and uses the
        latest full analysis of {sourceText} when it matches.
      </p>
      {error ? (
        <p className="form-error" role="alert" data-testid="fullscale-error">
          Could not run the checks: {error}
        </p>
      ) : null}
      {result && !result.valid ? (
        <div className="banner banner-error" role="alert" data-testid="fullscale-invalid">
          <div>
            <div className="banner-title">The full-scale checks could not run</div>
            <div className="small">{result.message}</div>
          </div>
        </div>
      ) : null}
      {result ? (
        <div className="fs-result" data-testid="fullscale-result">
          <p className="small muted" data-testid="fullscale-info">
            Checked {result.source === 'version' ? `v${result.version_number}` : 'the draft'} {formatDateTime(result.computed_at)}.{' '}
            {result.analysis_source === 'reused'
              ? `Uses the full analysis #${result.analysis_id}${result.parts === 'selected' ? ' with the parts chosen on the Parts tab' : ''}.`
              : 'No matching full analysis, so a quick analysis with generic parts was run; run the full analysis on the Design tab for the most accurate numbers.'}{' '}
            {result.catalogue_size > 0
              ? `Tubes and cells come from the ${result.catalogue_size}-part catalogue.`
              : 'The parts catalogue is empty, so no catalogue tubes or cells are compared.'}
          </p>
          {result.counts ? (
            <p className="fs-counts" data-testid="fullscale-counts">
              {result.counts.fail > 0 ? <StatusPill tone="error">{pluralize(result.counts.fail, 'check fails', 'checks fail')}</StatusPill> : null}{' '}
              {result.counts.warn > 0 ? <StatusPill tone="warn">{pluralize(result.counts.warn, 'warning')}</StatusPill> : null}{' '}
              {result.counts.fail === 0 && result.counts.warn === 0 ? <StatusPill tone="ok">✓ Every check passes</StatusPill> : null}
            </p>
          ) : null}
          {result.notes?.length ? (
            <ul className="small fs-notes">
              {result.notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          ) : null}
          <CheckList checks={result.checks} testId="fullscale-checks" />
          {result.mtow ? <MtowBlock mtow={result.mtow} layup={result.mtow_layup} /> : null}
          {result.motor_out ? <MotorOut mo={result.motor_out} /> : null}
          {result.structure ? <Structure s={result.structure} /> : null}
          {result.layup ? <Layup layup={result.layup} /> : null}
          {result.landing_gear ? <Gear gear={result.landing_gear} /> : null}
          {result.battery ? <Battery b={result.battery} /> : null}
          {result.range_endurance ? <RangeEndurance re={result.range_endurance} /> : null}
        </div>
      ) : null}
    </section>
  );
}
