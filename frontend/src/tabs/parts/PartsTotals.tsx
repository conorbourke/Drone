/**
 * Running totals of the parts list: cost against the prototype budget (an accessible meter with
 * a plain status), mass of the selected parts against the Tier 1 estimate of the same items,
 * take-off mass and endurance with the parts against the generic figures, unpriced lines and
 * the UK import note; and the "Upgrades worth paying for" list.
 */
import type { PartsList } from '../../api/partsList';
import { Explain } from '../../components/Explain';
import { formatEur, formatMassG, formatMassKg, formatWithUnit } from '../../lib/format';
import { BUDGET_STATUS, budgetGeometry, formatSignedEur, formatSignedG, formatSignedMin } from '../../lib/partsList';

function BudgetMeter({ cost, budget, status }: { cost: number; budget: number; status: 'under' | 'near' | 'over' }) {
  const g = budgetGeometry({ cost_eur: cost, budget_eur: budget });
  const st = BUDGET_STATUS[status];
  const pct = budget > 0 ? Math.round((cost / budget) * 100) : 0;
  return (
    <div className="budget">
      <div className="budget-head">
        <span className="budget-figure" data-testid="parts-total-cost" data-value={cost}>
          {formatEur(cost)}
        </span>
        <span className="small muted">
          of <span data-testid="parts-budget" data-value={budget}>{formatEur(budget)}</span> budget ({pct} %)
        </span>
        <span className={`pill pill-${st.tone} status-badge`} data-testid="parts-budget-status" data-status={status}>
          <span aria-hidden="true" className="status-icon">
            {st.icon}
          </span>
          {st.label}
        </span>
      </div>
      <div
        className={`budget-track budget-${status}`}
        role="meter"
        aria-label="Parts cost against the prototype budget"
        aria-valuemin={0}
        aria-valuemax={budget}
        aria-valuenow={Math.min(cost, budget)}
        aria-valuetext={`${formatEur(cost)} of ${formatEur(budget)}, ${pct} %: ${st.label.toLowerCase()}`}
        data-testid="parts-budget-bar"
        data-status={status}
        title={`${formatEur(cost)} of ${formatEur(budget)} (${pct} %)`}
      >
        <span className="budget-fill" style={{ width: `${g.fillPct}%` }} />
        {status === 'over' ? <span className="budget-limit" style={{ left: `${g.budgetPct}%` }} aria-hidden="true" /> : null}
      </div>
      <div className="budget-scale small faint" aria-hidden="true">
        <span>€0</span>
        <span>{formatEur(g.scaleMax).replace(/\.00$/, '')}</span>
      </div>
    </div>
  );
}

export function PartsTotals({ list }: { list: PartsList }) {
  const t = list.totals;
  const unpriced = t.unpriced_roles.map((r) => list.roles.find((x) => x.role === r)?.label ?? r);
  return (
    <section className="card parts-totals" data-testid="parts-totals" aria-labelledby="parts-totals-heading">
      <div className="card-header">
        <h2 id="parts-totals-heading">Totals</h2>
      </div>
      <BudgetMeter cost={t.cost_eur} budget={t.budget_eur} status={t.budget_status} />
      <p className="small" data-testid="parts-budget-message">
        {t.budget_message}
      </p>
      {unpriced.length > 0 ? (
        <p className="small muted" data-testid="parts-unpriced">
          Not counted (no price yet): {unpriced.join(', ')}.
        </p>
      ) : null}

      <dl className="kv-list parts-totals-kv">
        <div className="kv-row">
          <dt>
            Selected parts and consumables
            <Explain
              label="mass of the selected parts"
              text={`Installed mass of every part in the list plus the consumables line, compared with what the Tier 1 mass model estimates for the same items (statistical motor, ESC, propeller, battery and tube masses and the ${formatMassG(t.avionics_allowance_g)} avionics allowance). The avionics parts weigh ${formatMassG(t.avionics_parts_g)}.`}
            />
          </dt>
          <dd data-testid="parts-mass-total" data-value={t.mass_g}>
            {formatMassG(t.mass_g)}
          </dd>
        </div>
        <div className="kv-row">
          <dt>Tier 1 estimate for the same items</dt>
          <dd data-testid="parts-mass-tier1" data-value={t.tier1_mass_g}>
            {formatMassG(t.tier1_mass_g)}{' '}
            <span className="faint">({formatSignedG(t.mass_vs_tier1_g)} with parts)</span>
          </dd>
        </div>
        <div className="kv-row">
          <dt>
            Take-off mass with these parts
            <Explain
              label="take-off mass with parts"
              text="Take-off mass from the Tier 1 mass model with the selected parts' masses in place of the statistical ones (heaviest camera), against the same model with generic parts."
            />
          </dt>
          <dd data-testid="parts-takeoff" data-value={t.takeoff_mass_kg}>
            {formatMassKg(t.takeoff_mass_kg)} <span className="faint">(generic {formatMassKg(t.takeoff_mass_generic_kg)})</span>
          </dd>
        </div>
        <div className="kv-row">
          <dt>
            Estimated wing-flight endurance
            <Explain label="estimated endurance" text={t.endurance_note} />
          </dt>
          <dd data-testid="parts-endurance" data-value={t.estimated_endurance_min ?? ''}>
            {formatWithUnit(t.estimated_endurance_min, 'min', { maxFractionDigits: 0 })}{' '}
            {t.generic_endurance_min !== null ? (
              <span className="faint">(generic {formatWithUnit(t.generic_endurance_min, 'min', { maxFractionDigits: 0 })})</span>
            ) : null}
          </dd>
        </div>
      </dl>

      <p className="small muted parts-uk-note" data-testid="parts-uk-note">
        <strong>Buying from the UK:</strong> {list.uk_import_note}
      </p>
    </section>
  );
}

export function PartsUpgrades({ list }: { list: PartsList }) {
  if (list.upgrades.length === 0) {
    return (
      <section className="card" data-testid="parts-upgrades" aria-labelledby="parts-upgrades-heading">
        <div className="card-header">
          <h2 id="parts-upgrades-heading">Upgrades worth paying for</h2>
        </div>
        <p className="small muted">No catalogue part costs more and buys more endurance or margin for this design.</p>
      </section>
    );
  }
  return (
    <section className="card" data-testid="parts-upgrades" aria-labelledby="parts-upgrades-heading">
      <div className="card-header">
        <h2 id="parts-upgrades-heading">Upgrades worth paying for</h2>
        <Explain
          label="upgrades"
          text="Parts that cost more than the recommended ones and buy endurance, a lighter aircraft or more margin, best value first. € per minute is the extra cost for each minute of wing flight gained. Choose one with Replace on its line."
        />
      </div>
      <ul className="upgrade-list">
        {list.upgrades.map((u, index) => (
          <li key={`${u.role}-${u.part.id}-${index}`} className="upgrade-item" data-testid="parts-upgrade" data-role={u.role}>
            <div className="upgrade-title">
              <span className="upgrade-role">{u.role_label}</span>
              <span className="upgrade-name">{u.label || `${u.part.manufacturer} ${u.part.model}`}</span>
            </div>
            <dl className="upgrade-figures">
              <div>
                <dt>Extra cost</dt>
                <dd>{formatSignedEur(u.extra_cost_eur)}</dd>
              </div>
              <div>
                <dt>Mass</dt>
                <dd>{formatSignedG(u.mass_change_g)}</dd>
              </div>
              <div>
                <dt>Endurance</dt>
                <dd>{formatSignedMin(u.endurance_gain_min)}</dd>
              </div>
              <div>
                <dt>€ per minute</dt>
                <dd>{u.eur_per_min !== null ? formatEur(u.eur_per_min) : '—'}</dd>
              </div>
            </dl>
            <p className="small muted upgrade-trade">{u.trade_off}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}
