/**
 * Built weights: every component of the design's mass breakdown with the model's prediction
 * and a field for the weighed mass (g) and a note; totals against the prediction and the
 * structural mass factor that feeds the calibration.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { errorMessage, isAuthError } from '../../api/client';
import { getBuiltWeights, saveBuiltWeights, type BuiltWeights } from '../../api/flightData';
import { Explain } from '../../components/Explain';
import { useToast } from '../../components/Toast';
import { formatMassG } from '../../lib/format';
import { formatSignedPct, parseGrams } from '../../lib/flightData';

const GROUP_LABEL: Record<string, string> = {
  structure: 'Structure',
  propulsion: 'Propulsion',
  systems: 'Systems',
  energy: 'Battery',
};

interface Draft {
  measured: string;
  note: string;
}

export function BuiltWeightsForm({ projectId, onSaved }: { projectId: number; onSaved: () => void }) {
  const toast = useToast();
  const [data, setData] = useState<BuiltWeights | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [saving, setSaving] = useState(false);

  const adopt = useCallback((d: BuiltWeights) => {
    setData(d);
    setDrafts(
      Object.fromEntries(
        d.items.map((i) => [i.key, { measured: i.measured_g === null ? '' : String(i.measured_g), note: i.note }]),
      ),
    );
  }, []);

  useEffect(() => {
    getBuiltWeights(projectId)
      .then((d) => {
        adopt(d);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!isAuthError(e)) setError(errorMessage(e));
      });
  }, [projectId, adopt]);

  const invalid = useMemo(
    () => Object.entries(drafts).filter(([, d]) => Number.isNaN(parseGrams(d.measured) ?? 0)).map(([k]) => k),
    [drafts],
  );
  const dirty = useMemo(() => {
    if (!data) return false;
    return data.items.some((i) => {
      const d = drafts[i.key];
      if (!d) return false;
      return (parseGrams(d.measured) ?? null) !== i.measured_g || d.note !== i.note;
    });
  }, [data, drafts]);

  const save = () => {
    if (!data || invalid.length) return;
    setSaving(true);
    const items = data.items.map((i) => ({
      key: i.key,
      measured_g: parseGrams(drafts[i.key]?.measured ?? ''),
      note: drafts[i.key]?.note ?? '',
    }));
    saveBuiltWeights(projectId, items)
      .then((d) => {
        adopt(d);
        toast.success('Built weights saved.');
        onSaved();
      })
      .catch((e: unknown) => {
        if (!isAuthError(e)) toast.error(errorMessage(e));
      })
      .finally(() => setSaving(false));
  };

  const groups = data ? Array.from(new Set(data.items.map((i) => i.group))) : [];

  return (
    <section className="card" data-testid="built-weights" aria-labelledby="built-weights-heading">
      <div className="card-header">
        <h2 id="built-weights-heading">Built weights</h2>
        <Explain
          label="built weights"
          text="Weigh each part as built (a kitchen scale reading to 1 g is enough) and enter it here. The total is compared with the mass model, and the structure parts give the structural mass factor that the calibration can apply to the printed shell, wing, tail and booms."
        />
      </div>
      {error ? <p className="small field-error">{error}</p> : null}
      {data?.reason ? <p className="small muted">{data.reason}</p> : null}
      {data ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          {groups.map((g) => (
            <fieldset key={g} className="flight-weights-group">
              <legend>{GROUP_LABEL[g] ?? g}</legend>
              {data.items
                .filter((i) => i.group === g)
                .map((i) => {
                  const d = drafts[i.key] ?? { measured: '', note: '' };
                  const bad = invalid.includes(i.key);
                  return (
                    <div key={i.key} className="flight-weight-row">
                      <label className="flight-weight-label" htmlFor={`bw-${i.key}`}>
                        {i.label}
                        {i.explain ? <Explain label={i.label} text={i.explain} /> : null}
                        <span className="small faint"> predicted {formatMassG(i.predicted_g)}</span>
                      </label>
                      <div className="field-control flight-weight-input">
                        <input
                          id={`bw-${i.key}`}
                          inputMode="decimal"
                          placeholder="weighed"
                          value={d.measured}
                          aria-invalid={bad ? 'true' : undefined}
                          data-testid={`built-weight-${i.key}`}
                          onChange={(e) => setDrafts((s) => ({ ...s, [i.key]: { ...d, measured: e.target.value } }))}
                        />
                        <span className="field-unit">g</span>
                      </div>
                      <input
                        className="flight-weight-note"
                        aria-label={`Note for ${i.label}`}
                        placeholder="note (optional)"
                        maxLength={500}
                        value={d.note}
                        onChange={(e) => setDrafts((s) => ({ ...s, [i.key]: { ...d, note: e.target.value } }))}
                      />
                      {bad ? <p className="field-error small">Enter grams between 0 and 30 000, or leave it blank.</p> : null}
                    </div>
                  );
                })}
            </fieldset>
          ))}
          <dl className="kv-list" data-testid="built-weights-totals">
            <div className="kv-row">
              <dt>Weighed items</dt>
              <dd>
                {data.totals.weighed_items} of {data.totals.items}
              </dd>
            </div>
            <div className="kv-row">
              <dt>
                Weighed total against predicted
                <Explain label="weighed total" text="Sum of the weighed parts against the model's prediction for the same parts only." />
              </dt>
              <dd data-testid="built-weights-total">
                {formatMassG(data.totals.measured_g)} vs {formatMassG(data.totals.predicted_weighed_g)}{' '}
                <span className="faint">({formatSignedPct(data.totals.difference_pct)})</span>
              </dd>
            </div>
            <div className="kv-row">
              <dt>
                Structural mass factor
                <Explain label="structural mass factor" text="Weighed ÷ predicted mass of the structure parts (printed shell, wing, tail, booms). 1.10 means the structure came out 10 % heavier than the model expects; the Calibration card can apply it." />
              </dt>
              <dd data-testid="built-weights-structural">
                {data.structural.valid && data.structural.value !== undefined
                  ? `× ${data.structural.value.toFixed(3)} ± ${(data.structural.uncertainty ?? 0).toFixed(3)}`
                  : (data.structural.reason ?? '—')}
              </dd>
            </div>
          </dl>
          <div className="flight-actions">
            <button type="submit" className="button button-primary" disabled={saving || !dirty || invalid.length > 0} data-testid="built-weights-save">
              {saving ? 'Saving…' : 'Save built weights'}
            </button>
          </div>
        </form>
      ) : !error ? (
        <p className="small muted">Loading the mass breakdown…</p>
      ) : null}
    </section>
  );
}
