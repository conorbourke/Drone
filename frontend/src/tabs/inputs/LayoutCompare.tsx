/**
 * The current design re-estimated for each of the three supported layouts, side by side:
 * take-off mass, endurance range, hover and cruise power, complexity with reasons, the
 * ArduPilot note and the adjustments made to compare fairly, with "Use this layout".
 */
import { useMemo } from 'react';
import type { DraftDocument, Settings } from '../../api/types';
import { Explain } from '../../components/Explain';
import { StatusPill } from '../../components/StatusPill';
import { compareLayouts, type AirfoilSummaryMap } from '../../engine';
import { explainQuantity, quantityText } from '../../panels/EstimatesPanel';

const COMPLEXITY_TONE = { low: 'ok', medium: 'info', high: 'warn' } as const;

export function LayoutCompare({
  doc,
  settings,
  airfoils,
  update,
}: {
  doc: DraftDocument;
  settings: Settings | null;
  airfoils: AirfoilSummaryMap;
  update: (path: string, value: unknown) => void;
}) {
  const results = useMemo(
    () => (settings ? compareLayouts({ parameters: doc.parameters, mission: doc.mission, settings, airfoils }) : null),
    [doc.parameters, doc.mission, settings, airfoils],
  );
  const current = doc.parameters.layout;

  return (
    <section className="card" aria-labelledby="layout-compare-heading" data-testid="layout-compare">
      <div className="card-header">
        <h2 id="layout-compare-heading">Compare layouts</h2>
      </div>
      <p className="card-note">
        Your current design re-estimated with each layout ArduPilot can fly. Battery position and tilt axis are adjusted
        so each comparison is fair; the adjustments are listed and are not applied to your design.
      </p>
      {!results ? (
        <div className="skeleton" aria-busy="true" aria-label="Loading" />
      ) : (
        <div className="layout-grid">
          {results.map((r) => {
            const isCurrent = r.layout === current;
            return (
              <article key={r.layout} className={`layout-option${isCurrent ? ' is-current' : ''}`} data-layout={r.layout}>
                <header className="layout-option-head">
                  <h3>{r.label}</h3>
                  {isCurrent ? <StatusPill tone="info">Current</StatusPill> : null}
                </header>
                <dl className="layout-kv">
                  {[
                    { label: 'Take-off mass', q: r.takeoff_mass, rangeFirst: false },
                    { label: 'Endurance', q: r.endurance, rangeFirst: true },
                    { label: 'Hover power', q: r.hover_power, rangeFirst: false },
                    { label: 'Cruise power', q: r.cruise_power, rangeFirst: false },
                  ].map((m) => (
                    <div key={m.label} className="kv-row">
                      <dt>
                        {m.label} <Explain label={`${r.label} ${m.label}`} text={explainQuantity(m.q)} />
                      </dt>
                      <dd>{quantityText(m.q, { rangeFirst: m.rangeFirst })}</dd>
                    </div>
                  ))}
                  <div className="kv-row">
                    <dt>Complexity</dt>
                    <dd>
                      <StatusPill tone={COMPLEXITY_TONE[r.complexity.rating]}>{r.complexity.rating}</StatusPill>
                    </dd>
                  </div>
                </dl>
                <ul className="small layout-reasons">
                  {r.complexity.reasons.map((reason, i) => (
                    <li key={i}>{reason}</li>
                  ))}
                </ul>
                <p className="small layout-ardupilot">
                  <strong>ArduPilot:</strong> {r.ardupilot_note}
                </p>
                {r.adjustments.length > 0 ? (
                  <details className="small">
                    <summary>Adjusted to compare ({r.adjustments.length})</summary>
                    <ul>
                      {r.adjustments.map((a, i) => (
                        <li key={i}>{a}</li>
                      ))}
                    </ul>
                  </details>
                ) : null}
                {r.problems.length > 0 ? (
                  <details className="small">
                    <summary>
                      {r.problems.length} {r.problems.length === 1 ? 'check needs' : 'checks need'} attention
                    </summary>
                    <ul>
                      {r.problems.map((p) => (
                        <li key={p.key}>
                          <strong>{p.label}:</strong> {p.message}
                        </li>
                      ))}
                    </ul>
                  </details>
                ) : null}
                <button
                  type="button"
                  className={`button button-sm${isCurrent ? '' : ' button-primary'}`}
                  data-testid={`layout-use-${r.layout}`}
                  disabled={isCurrent}
                  onClick={() => update('parameters.layout', r.layout)}
                >
                  {isCurrent ? 'In use' : 'Use this layout'}
                </button>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
