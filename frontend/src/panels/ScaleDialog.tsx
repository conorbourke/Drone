/**
 * "Scale to weight": a target take-off mass → the server re-solves the same layout for it
 * (wing loading, propellers, battery, structure; never one factor for every dimension), runs the
 * full analysis, and this dialog shows the before/after table with the reason for each row,
 * the notes and the scaled design's checks, with "Save as new version" (explicit parameters and
 * mission through POST /versions, since the full document is larger than a from-patch patch).
 */
import { useId, useState } from 'react';
import type { FormEvent } from 'react';
import type { AnalysisListItem, ScaleResult, ScaleRow } from '../api/analysis';
import { api, errorMessage, isAuthError } from '../api/client';
import type { Settings } from '../api/types';
import { isActive, useAnalysisJob } from '../api/useAnalysisJob';
import { createVersionFromDocuments } from '../api/versions';
import { CheckList } from '../components/CheckList';
import { Modal } from '../components/Modal';
import { formatSig } from '../lib/analysis';
import type { DraftDocument } from '../api/types';
import { formatMassKg } from '../lib/format';
import { useWorkspace } from '../lib/workspace';
import { ServerMetric } from './AnalysisResultView';

const MIN_KG = 0.2;
const MAX_KG = 25;

function cell(v: number | string | null, unit: string): string {
  if (v === null || v === undefined) return '–';
  if (typeof v === 'string') return v;
  return `${formatSig(v)}${unit ? ` ${unit}` : ''}`;
}

function ScaleTable({ rows }: { rows: ScaleRow[] }) {
  return (
    <div className="table-scroll">
      <table className="table table-compact scale-table" data-testid="scale-table">
        <thead>
          <tr>
            <th scope="col">What</th>
            <th scope="col" className="num">
              Before
            </th>
            <th scope="col" className="num">
              After
            </th>
            <th scope="col">What changed and why</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} data-row={r.key}>
              <th scope="row">{r.label}</th>
              <td className="num">{cell(r.before, r.unit)}</td>
              <td className="num">{cell(r.after, r.unit)}</td>
              <td className="why-cell small">{r.why}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ScaleDialog({
  open,
  onClose,
  doc,
  settings,
}: {
  open: boolean;
  onClose: () => void;
  doc: DraftDocument;
  settings: Settings | null;
}) {
  const workspace = useWorkspace();
  const inputId = useId();
  const nameId = useId();
  // null until the owner types: the field then shows the mission's current target.
  const [typed, setTyped] = useState<string | null>(null);
  const [jobId, setJobId] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const { job } = useAnalysisJob<ScaleResult>(jobId);

  const currentTarget = (doc.mission as { target_takeoff_mass_kg?: number }).target_takeoff_mass_kg;
  const text = typed ?? (currentTarget !== undefined && Number.isFinite(currentTarget) ? String(currentTarget) : '5');

  const mass = Number(text);
  const valid = text.trim() !== '' && Number.isFinite(mass) && mass >= MIN_KG && mass <= MAX_KG;
  const warnKg = settings?.limits.warn_mtow_kg ?? 23;
  const designKg = settings?.limits.design_mtow_kg ?? 24;
  const running = isActive(job?.status) || starting;
  const result = job?.status === 'done' ? job.result : null;

  const run = async (event: FormEvent) => {
    event.preventDefault();
    if (!valid) {
      setError(`Enter a take-off mass between ${MIN_KG} and ${MAX_KG} kg.`);
      return;
    }
    setStarting(true);
    setError(null);
    setSaved(null);
    try {
      await workspace.flushDraft();
      const started = await api<AnalysisListItem>(`/api/projects/${workspace.projectId}/scale`, {
        method: 'POST',
        body: { target_takeoff_mass_kg: mass, source: 'draft' },
      });
      setJobId(started.id);
      setName(`Scaled to ${formatSig(mass, 1)} kg`);
    } catch (caught) {
      if (!isAuthError(caught)) setError(errorMessage(caught));
    } finally {
      setStarting(false);
    }
  };

  const save = async () => {
    if (!result?.parameters || !result.mission) return;
    setSaving(true);
    setError(null);
    try {
      const version = await createVersionFromDocuments(
        workspace.projectId,
        {
          name: name.trim() || `Scaled to ${formatSig(result.target_takeoff_mass_kg, 1)} kg`,
          notes: `Scaled from the draft to ${formatSig(result.target_takeoff_mass_kg, 1)} kg take-off mass.${result.notes?.length ? `\n\n${result.notes.join('\n')}` : ''}`,
          parameters: result.parameters,
          mission: result.mission,
        },
        (workspace.versions ?? []).map((v) => v.name),
      );
      setSaved(`Saved as v${version.number} “${version.name}”.`);
      await workspace.versionCreated(version, workspace.basisNumber);
    } catch (caught) {
      if (!isAuthError(caught)) setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  };

  const pct = job ? Math.round(job.progress * 100) : 0;

  return (
    <Modal
      open={open}
      title="Scale to weight"
      onClose={onClose}
      className="modal-wide"
      testId="scale-dialog"
      footer={
        <>
          <button type="button" className="button" onClick={onClose}>
            Close
          </button>
          {result?.valid ? (
            <button
              type="button"
              className="button button-primary"
              data-testid="scale-save"
              disabled={saving || !!saved}
              onClick={() => void save()}
            >
              {saving ? 'Saving…' : saved ? 'Saved' : 'Save as new version'}
            </button>
          ) : null}
        </>
      }
    >
      <p className="small muted">
        Re-designs the same layout for a new take-off mass: the wing for the stall margin and best lift-to-drag, propellers and
        motors for the hover thrust-to-weight, the battery for the endurance target, booms and spar for the structure checks.
        Dimensions are never multiplied by one factor. The draft is not changed.
      </p>
      <form className="scale-form" onSubmit={(e) => void run(e)}>
        <div className="form-row">
          <label htmlFor={inputId}>Target take-off mass (kg)</label>
          <div className="row">
            <input
              id={inputId}
              className="input"
              type="number"
              inputMode="decimal"
              min={MIN_KG}
              max={MAX_KG}
              step={0.1}
              value={text}
              data-testid="scale-target"
              aria-invalid={!valid && text !== '' ? true : undefined}
              onChange={(e) => setTyped(e.target.value)}
            />
            <button type="submit" className="button button-primary" data-testid="scale-run" disabled={running}>
              {running ? 'Scaling…' : 'Scale'}
            </button>
          </div>
        </div>
        {valid && mass >= warnKg ? (
          <p
            className={`banner ${mass > designKg ? 'banner-error' : 'banner-warn'} small`}
            data-testid="scale-warning"
            role="status"
          >
            {mass > designKg
              ? `${formatMassKg(mass)} is above the design limit of ${formatMassKg(designKg)}; the scaled design will fail the mass check. The legal limit (EU Open A3) is 25 kg.`
              : `${formatMassKg(mass)} is close to the design limit of ${formatMassKg(designKg)} (warnings from ${formatMassKg(warnKg)}).`}
          </p>
        ) : null}
      </form>
      {error ? (
        <p className="form-error" role="alert" data-testid="scale-error">
          {error}
        </p>
      ) : null}

      {job && isActive(job.status) ? (
        <div className="analysis-progress" data-testid="scale-progress" role="status">
          <div
            className="progress-track"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct}
            aria-label="Scaling progress"
          >
            <span className="progress-fill" style={{ width: `${Math.max(3, pct)}%` }} />
          </div>
          <p className="small">
            {job.stage || 'Working…'} <span className="muted">({pct} %)</span>
          </p>
        </div>
      ) : null}
      {job?.status === 'error' ? (
        <p className="form-error" role="alert" data-testid="scale-error">
          Scaling failed: {job.error}
        </p>
      ) : null}

      {result ? (
        <div className="stack-sm" data-testid="scale-result">
          {!result.valid ? <p className="form-error">{result.message ?? 'This design could not be scaled.'}</p> : null}
          {result.analysis?.summary ? (
            <div className="metric-grid analysis-metrics">
              <ServerMetric id="scale-takeoff_mass" q={result.analysis.summary.takeoff_mass} label="Take-off mass" />
              <ServerMetric
                id="scale-endurance"
                q={result.analysis.summary.endurance_cruise}
                label="Wing-flight endurance"
                rangeFirst
                digits={0}
              />
              <ServerMetric id="scale-cruise_power" q={result.analysis.summary.cruise_power} label="Cruise power" digits={0} />
              <ServerMetric id="scale-hover_power" q={result.analysis.summary.hover_power} label="Hover power" digits={0} />
            </div>
          ) : null}
          {result.table?.length ? <ScaleTable rows={result.table} /> : null}
          {result.notes?.length ? (
            <ul className="assumptions small" data-testid="scale-notes">
              {result.notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          ) : null}
          {result.checks?.length ? (
            <>
              <h3 className="metric-group-title">Checks of the scaled design</h3>
              <CheckList checks={result.checks} testId="scale-checks" />
            </>
          ) : null}
          {result.valid ? (
            <div className="form-row">
              <label htmlFor={nameId}>Name for the new version</label>
              <input
                id={nameId}
                className="input"
                data-testid="scale-name"
                value={name}
                maxLength={120}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
          ) : null}
          {saved ? (
            <p className="small" role="status" data-testid="scale-saved">
              {saved}
            </p>
          ) : null}
        </div>
      ) : null}
    </Modal>
  );
}
