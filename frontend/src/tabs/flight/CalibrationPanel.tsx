/**
 * Calibration: the factors the project's flights and built weights give, what applying them
 * would change in the draft's headline numbers, Apply and Undo.
 */
import { useCallback, useEffect, useState } from 'react';
import { errorMessage, isAuthError } from '../../api/client';
import {
  applyCalibration,
  getCalibration,
  previewCalibration,
  undoCalibration,
  type CalibrationPreview,
  type CalibrationState,
} from '../../api/flightData';
import { Explain } from '../../components/Explain';
import { StatusPill } from '../../components/StatusPill';
import { useToast } from '../../components/Toast';
import { formatDateTime } from '../../lib/format';
import { formatMeasure, formatSignedPct } from '../../lib/flightData';

function factorText(value: number | undefined, unc: number | undefined): string {
  if (value === undefined || !Number.isFinite(value)) return '—';
  const v = `× ${value.toFixed(3)}`;
  return unc !== undefined && Number.isFinite(unc) ? `${v} ± ${unc.toFixed(3)}` : v;
}

export function CalibrationPanel({ projectId, refreshKey }: { projectId: number; refreshKey: number }) {
  const toast = useToast();
  const [state, setState] = useState<CalibrationState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [previewState, setPreview] = useState<{ key: number; data: CalibrationPreview } | null>(null);
  const preview = previewState && previewState.key === refreshKey ? previewState.data : null;
  const [previewing, setPreviewing] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    getCalibration(projectId)
      .then((s) => {
        setState(s);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!isAuthError(e)) setError(errorMessage(e));
      });
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  const offered = state ? Object.values(state.proposed.factors).filter((f) => f.applicable) : [];

  const showPreview = () => {
    setPreviewing(true);
    previewCalibration(projectId)
      .then((data) => setPreview({ key: refreshKey, data }))
      .catch((e: unknown) => {
        if (!isAuthError(e)) toast.error(errorMessage(e));
      })
      .finally(() => setPreviewing(false));
  };

  const apply = () => {
    setBusy(true);
    applyCalibration(projectId)
      .then((s) => {
        setState(s);
        toast.success('Calibration applied. Run the analysis again on the Design tab to see the calibrated numbers.');
      })
      .catch((e: unknown) => {
        if (!isAuthError(e)) toast.error(errorMessage(e));
      })
      .finally(() => setBusy(false));
  };

  const undo = () => {
    setBusy(true);
    undoCalibration(projectId)
      .then((s) => {
        setState(s);
        toast.info('Calibration removed. Analyses use the uncalibrated model again.');
      })
      .catch((e: unknown) => {
        if (!isAuthError(e)) toast.error(errorMessage(e));
      })
      .finally(() => setBusy(false));
  };

  return (
    <section className="card" data-testid="calibration-panel" aria-labelledby="calibration-heading">
      <div className="card-header">
        <h2 id="calibration-heading">Calibration</h2>
        <Explain
          label="calibration"
          text={
            'A calibration factor multiplies one of the model’s predictions so it matches what was measured (1.000 means the model was right). Several flights are combined, weighting each by how precise it is; the ± is the remaining uncertainty.\n\nApplied factors are used by every analysis run afterwards, which then says "Calibrated with N flights". Undo returns to the uncalibrated model.'
          }
        />
        {state?.applied ? (
          <StatusPill tone="ok" testId="calibration-applied">
            Applied · {state.applied.n_logs} flight{state.applied.n_logs === 1 ? '' : 's'}
          </StatusPill>
        ) : (
          <StatusPill tone="neutral" testId="calibration-applied">
            Not applied
          </StatusPill>
        )}
      </div>
      {error ? <p className="small field-error">{error}</p> : null}
      {state ? (
        <>
          <div className="table-scroll">
            <table className="table table-compact">
              <thead>
                <tr>
                  <th>Factor</th>
                  <th className="num">From the flights</th>
                  <th className="num">Applied now</th>
                  <th>What it changes</th>
                </tr>
              </thead>
              <tbody>
                {Object.values(state.proposed.factors).map((f) => {
                  const applied = state.applied?.factors[f.name];
                  return (
                    <tr key={f.name} data-testid="calibration-factor" data-name={f.name} data-applicable={f.applicable ? 'true' : 'false'}>
                      <th scope="row">
                        {f.label}
                        <Explain label={f.label} text={f.explain} />
                      </th>
                      <td className="num">
                        {f.valid ? factorText(f.value, f.uncertainty) : '—'}
                        {f.valid && f.n_logs ? <span className="faint small"> ({f.n_logs} log{f.n_logs === 1 ? '' : 's'})</span> : null}
                      </td>
                      <td className="num">{applied ? factorText(applied.value, applied.uncertainty) : '—'}</td>
                      <td className="small">
                        {f.applicable ? f.effect : <span className="muted">{f.why_not ?? f.reason}</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {state.proposed.notes.length ? (
            <ul className="small muted">
              {state.proposed.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          ) : null}

          {preview ? (
            <div data-testid="calibration-preview">
              <h3 className="flight-subheading">What applying changes (the draft, quick analysis)</h3>
              {preview.rows.length ? (
                <div className="table-scroll">
                  <table className="table table-compact">
                    <thead>
                      <tr>
                        <th>Number</th>
                        <th className="num">Now</th>
                        <th className="num">Calibrated</th>
                        <th className="num">Change</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.rows.map((r) => (
                        <tr key={r.key} data-testid="calibration-preview-row" data-key={r.key}>
                          <th scope="row">
                            {r.label}
                            {r.explain ? <Explain label={r.label} text={r.explain} /> : null}
                          </th>
                          <td className="num">{formatMeasure(r.before, r.unit)}</td>
                          <td className="num">{formatMeasure(r.after, r.unit)}</td>
                          <td className="num">{formatSignedPct(r.change_pct)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="small muted">{preview.reason ?? 'Nothing would change.'}</p>
              )}
            </div>
          ) : null}

          <div className="flight-actions">
            <button
              type="button"
              className="button"
              onClick={showPreview}
              disabled={previewing || offered.length === 0}
              data-testid="calibration-preview-button"
            >
              {previewing ? 'Working it out…' : 'Show what changes'}
            </button>
            <button
              type="button"
              className="button button-primary"
              onClick={apply}
              disabled={busy || offered.length === 0}
              data-testid="calibration-apply"
            >
              Apply calibration{offered.length ? ` (${offered.length} factor${offered.length === 1 ? '' : 's'})` : ''}
            </button>
            {state.applied ? (
              <button type="button" className="button" onClick={undo} disabled={busy} data-testid="calibration-undo">
                Undo calibration
              </button>
            ) : null}
          </div>
          {offered.length === 0 ? (
            <p className="small muted">
              Nothing to apply yet: read a flight with steady hover or cruise, or weigh the structure parts below.
            </p>
          ) : null}
          {state.applied ? (
            <p className="small muted">Applied {formatDateTime(state.applied.applied_at)}. Run the analysis again on the Design tab to use it.</p>
          ) : null}
        </>
      ) : !error ? (
        <p className="small muted">Loading…</p>
      ) : null}
    </section>
  );
}
