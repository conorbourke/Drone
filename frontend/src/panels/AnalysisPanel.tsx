/**
 * Full analysis on the Design tab (docs/phases/PHASE3.md section 6): "Analyse" the draft or a
 * saved version, progress while the server works (queue position, stage, bar), then the results
 * (AnalysisResultView) and the ranked recommendations and fixes, each with "Try as new version".
 * The analysis shown is the newest full analysis of the project; a draft analysis is marked
 * stale as soon as the draft is edited after it was queued.
 */
import { useEffect, useId, useState } from 'react';
import { Link } from 'react-router';
import type { AnalysisListItem, AnalysisResult, Recommendation, ServerQuantity } from '../api/analysis';
import { api, errorMessage, isAbortError, isAuthError } from '../api/client';
import type { Settings, VersionSummary } from '../api/types';
import { isActive, useAnalysisJob } from '../api/useAnalysisJob';
import { Explain } from '../components/Explain';
import { StatusPill } from '../components/StatusPill';
import { formatSig, isAnalysisStale, quantityShort, recommendationVersionName } from '../lib/analysis';
import type { DraftDocument } from '../api/types';
import { formatDateTime } from '../lib/format';
import { useWorkspace } from '../lib/workspace';
import { AnalysisResultView } from './AnalysisResultView';
import { ScaleDialog } from './ScaleDialog';

const KEY_LABELS: Record<string, string> = {
  endurance_cruise: 'Endurance',
  takeoff_mass: 'Take-off mass',
  cruise_power: 'Cruise power',
  hover_power: 'Hover power',
  stall_speed: 'Stall speed',
  static_margin_min_payload: 'Static margin (light)',
  lift_to_drag: 'Lift-to-drag',
};

function KeyNumbersTable({ rec }: { rec: Recommendation }) {
  const keys = Object.keys(KEY_LABELS).filter((k) => rec.before?.[k] && rec.after?.[k]);
  if (!keys.length) return null;
  const v = (q: ServerQuantity) => `${formatSig(q.value ?? NaN)}${q.unit ? ` ${q.unit}` : ''}`;
  return (
    <div className="table-scroll">
      <table className="table table-compact rec-table">
        <thead>
          <tr>
            <th />
            <th className="num">Before</th>
            <th className="num">After</th>
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => {
            const b = rec.before[k];
            const a = rec.after[k];
            const changed = Math.abs((a.value ?? 0) - (b.value ?? 0)) > 1e-6 * Math.max(1, Math.abs(b.value ?? 0));
            return (
              <tr key={k} className={changed ? 'is-diff' : undefined}>
                <th scope="row">{KEY_LABELS[k]}</th>
                <td className="num">{v(b)}</td>
                <td className="num">{v(a)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function RecommendationItem({
  rec,
  kind,
  busy,
  onTry,
}: {
  rec: Recommendation;
  kind: 'recommendation' | 'fix';
  busy: boolean;
  onTry: () => void;
}) {
  const before = rec.before?.endurance_cruise;
  const after = rec.after?.endurance_cruise;
  const gain = rec.endurance_gain_min;
  const gainLow = before && after && after.low !== null && before.low !== null ? after.low - before.low : null;
  const gainHigh = before && after && after.high !== null && before.high !== null ? after.high - before.high : null;
  return (
    <li
      className="rec-item"
      data-testid={kind === 'fix' ? 'fix-item' : 'recommendation'}
      data-key={rec.key}
      data-rank={rec.rank ?? ''}
    >
      <div className="rec-head">
        {kind === 'recommendation' && rec.rank ? <span className="rec-rank">{rec.rank}</span> : null}
        <p className="rec-sentence">{rec.sentence}</p>
      </div>
      {kind === 'recommendation' && gain !== undefined ? (
        <p className="rec-gain small">
          <StatusPill tone="ok">
            Endurance {gain >= 0 ? '+' : '−'}
            {formatSig(Math.abs(gain), 1)} min
          </StatusPill>{' '}
          {gainLow !== null && gainHigh !== null ? (
            <span className="muted">
              range {quantityShort(before, 0)} → {quantityShort(after, 0)}
            </span>
          ) : null}
        </p>
      ) : null}
      <details className="breakdown">
        <summary>Before and after</summary>
        <KeyNumbersTable rec={rec} />
      </details>
      <div className="rec-actions">
        <button
          type="button"
          className="button button-sm"
          data-testid={kind === 'fix' ? 'fix-try' : 'recommendation-try'}
          disabled={busy}
          onClick={onTry}
        >
          {busy ? 'Saving…' : 'Try as new version'}
        </button>
      </div>
    </li>
  );
}

function ProgressBar({ job }: { job: AnalysisListItem }) {
  const pct = Math.round(Math.max(0, Math.min(1, job.progress)) * 100);
  return (
    <div className="analysis-progress" data-testid="analysis-progress" data-status={job.status} role="status" aria-live="polite">
      <div
        className="progress-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-label="Analysis progress"
      >
        <span className="progress-fill" style={{ width: `${Math.max(3, pct)}%` }} />
      </div>
      <p className="small">
        <span data-testid="analysis-stage">
          {job.stage || (job.status === 'queued' ? 'Waiting for the analysis engine' : 'Working…')}
        </span>{' '}
        <span className="muted">({pct} %)</span>
        {job.status === 'queued' && job.queue_position !== null ? (
          <span className="muted" data-testid="analysis-queue">
            {' '}
            · {job.queue_position === 0 ? 'next in the queue' : `${job.queue_position} ahead in the queue`}
          </span>
        ) : null}
      </p>
    </div>
  );
}

export function AnalysisPanel({
  doc,
  versions,
  settings,
}: {
  doc: DraftDocument;
  versions: VersionSummary[] | null;
  settings: Settings | null;
}) {
  const workspace = useWorkspace();
  const { projectId } = workspace;
  const selectId = useId();
  const [source, setSource] = useState<string>('draft');
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [listLoaded, setListLoaded] = useState(false);
  const [trying, setTrying] = useState<string | null>(null);
  const [scaleOpen, setScaleOpen] = useState(false);
  const { job, error: pollError } = useAnalysisJob<AnalysisResult>(currentId);

  // The newest full analysis of the project, if any.
  useEffect(() => {
    const controller = new AbortController();
    api<AnalysisListItem[]>(`/api/projects/${projectId}/analyses?limit=20`, { signal: controller.signal })
      .then((list) => {
        const latest = list.find((a) => a.kind === 'full');
        if (latest) setCurrentId((id) => id ?? latest.id);
        setListLoaded(true);
      })
      .catch((caught: unknown) => {
        if (isAbortError(caught) || isAuthError(caught)) return;
        setListLoaded(true);
      });
    return () => controller.abort();
  }, [projectId]);

  // A selected version that was deleted falls back to the draft.
  const selectedVersion = source === 'draft' ? null : (versions?.find((v) => String(v.id) === source) ?? null);
  const effectiveSource = selectedVersion ? String(selectedVersion.id) : 'draft';

  const run = async (target: string) => {
    setStarting(true);
    setStartError(null);
    try {
      if (target === 'draft') await workspace.flushDraft();
      const body =
        target === 'draft' ? { source: 'draft', kind: 'full' } : { source: { version_id: Number(target) }, kind: 'full' };
      const started = await api<AnalysisListItem>(`/api/projects/${projectId}/analyses`, { method: 'POST', body });
      setCurrentId(started.id);
    } catch (caught) {
      if (!isAuthError(caught)) setStartError(errorMessage(caught));
    } finally {
      setStarting(false);
    }
  };

  const result = job?.result ?? null;
  const running = isActive(job?.status);
  const stale = !!job && !!result && isAnalysisStale(job, { status: workspace.draftStatus, savedAt: workspace.draftSavedAt });
  const recs = result?.recommendations ?? null;
  const sourceText = job
    ? job.source === 'version'
      ? `v${job.version_number ?? '?'}${versions?.find((v) => v.id === job.version_id)?.name ? ` “${versions.find((v) => v.id === job.version_id)!.name}”` : ''}`
      : 'the draft'
    : '';
  // The version a new one is compared with in the toast: the analysed version, else the draft's basis.
  const compareWith = job?.source === 'version' ? job.version_number : workspace.basisNumber;

  const tryRec = async (rec: Recommendation, kind: 'recommendation' | 'fix') => {
    if (!job) return;
    setTrying(`${kind}:${rec.key}`);
    await workspace.tryAsNewVersion({
      name: recommendationVersionName(rec, kind === 'fix' ? 'Fix' : 'Rec'),
      notes: rec.sentence,
      base: job.source === 'version' && job.version_id !== null ? { version_id: job.version_id } : 'draft',
      patch: rec.patch,
      compareWith,
    });
    setTrying(null);
  };

  const recommendations = result ? (
    result.valid ? (
      <section className="analysis-section" data-testid="analysis-recommendations" aria-labelledby="recs-heading">
        <h3 id="recs-heading">
          Recommendations{' '}
          <Explain
            label="recommendations"
            text={
              (recs?.method ??
                'Each key parameter is nudged in both directions and the fast analysis re-run; changes that make any check worse are dropped, the rest ranked by endurance gained.') +
              '\n\n"Try as new version" saves the change as a new version; the draft is not touched.'
            }
          />
        </h3>
        {recs === null ? (
          <p className="small muted" data-testid="recommendations-pending">
            Finding improvements… The results above are final; suggestions appear here when the sweep finishes.
          </p>
        ) : !recs.valid ? (
          <p className="small muted" data-testid="recommendations-empty">
            {recs.message ?? 'No suggestions for an invalid design.'}
          </p>
        ) : recs.recommendations.length === 0 ? (
          <p className="small muted" data-testid="recommendations-empty">
            No change tried ({recs.variants_tried ?? 0} variants) gains worthwhile endurance without making a check worse.
          </p>
        ) : (
          <ol className="rec-list">
            {recs.recommendations.map((rec) => (
              <RecommendationItem
                key={rec.key}
                rec={rec}
                kind="recommendation"
                busy={trying === `recommendation:${rec.key}`}
                onTry={() => void tryRec(rec, 'recommendation')}
              />
            ))}
          </ol>
        )}
        {recs?.fixes?.length ? (
          <div data-testid="analysis-fixes">
            <h3>Fixes for failing checks</h3>
            <p className="card-note">Changes that repair a failing check (verified with the same fast analysis).</p>
            <ul className="rec-list">
              {recs.fixes.map((fx) => (
                <RecommendationItem
                  key={fx.key}
                  rec={fx}
                  kind="fix"
                  busy={trying === `fix:${fx.key}`}
                  onTry={() => void tryRec(fx, 'fix')}
                />
              ))}
            </ul>
          </div>
        ) : null}
      </section>
    ) : null
  ) : null;

  return (
    <section className="card analysis-panel" data-testid="analysis-panel" aria-labelledby="analysis-heading">
      <div className="card-header">
        <h2 id="analysis-heading">Full analysis</h2>
        <Link to="/validation" className="button button-ghost button-sm" data-testid="validation-link">
          How accurate is it?
        </Link>
        <button type="button" className="button button-sm" data-testid="scale-open" onClick={() => setScaleOpen(true)}>
          Scale to weight…
        </button>
      </div>
      <p className="card-note">
        The server analysis (Tier 2): vortex-lattice aerodynamics, airfoil polars, motor, propeller and battery models, the
        transition and the structure. It takes about half a minute; the instant estimates above stay live while you edit.
      </p>

      <div className="analysis-controls">
        <label htmlFor={selectId} className="small muted">
          Analyse
        </label>
        <select
          id={selectId}
          className="input analysis-source"
          data-testid="analyse-source"
          value={effectiveSource}
          onChange={(e) => setSource(e.target.value)}
        >
          <option value="draft">The draft</option>
          {(versions ?? []).map((v) => (
            <option key={v.id} value={String(v.id)}>
              v{v.number} {v.name}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="button button-primary"
          data-testid="analyse-run"
          disabled={starting || running}
          onClick={() => void run(effectiveSource)}
        >
          {starting ? 'Starting…' : running ? 'Analysing…' : 'Analyse'}
        </button>
      </div>
      {startError ? (
        <p className="form-error" role="alert" data-testid="analysis-error">
          {startError}
        </p>
      ) : null}

      {job && running ? <ProgressBar job={job} /> : null}
      {pollError && running ? <p className="small muted">Lost contact with the server ({pollError}); retrying…</p> : null}

      {job?.status === 'error' ? (
        <div className="banner banner-error" role="alert" data-testid="analysis-error">
          <div>
            <div className="banner-title">The analysis failed</div>
            <div className="small">{job.error ?? 'Unknown error.'}</div>
          </div>
        </div>
      ) : null}

      {!job && listLoaded && currentId === null ? (
        <p className="small muted" data-testid="analysis-empty">
          No analysis yet. Press Analyse to run the full analysis on the draft.
        </p>
      ) : null}

      {job && result ? (
        <>
          <p className="analysis-source-info small" data-testid="analysis-source-info" data-source={job.source}>
            Analysis of <strong>{sourceText}</strong>
            {job.started_at ? `, run ${formatDateTime(job.started_at)}` : ''}
            {job.duration_s !== null && job.duration_s > 0 ? ` in ${formatSig(job.duration_s, 0)} s` : ''}
            {job.reused_from_id ? ' (reused an identical earlier analysis)' : ''}.
          </p>
          {stale ? (
            <div className="banner banner-warn" role="status" data-testid="analysis-stale">
              <div>
                <div className="banner-title">The draft has changed since this analysis ran</div>
                <div className="small">These numbers describe the draft as it was. Run it again to see the current design.</div>
              </div>
              <button
                type="button"
                className="button button-sm"
                data-testid="analysis-rerun"
                disabled={starting || running}
                onClick={() => void run('draft')}
              >
                Re-run
              </button>
            </div>
          ) : null}

          <div className={stale ? 'analysis-stale-body' : undefined}>
            <AnalysisResultView result={result} afterChecks={recommendations} />
          </div>
        </>
      ) : null}

      <ScaleDialog open={scaleOpen} onClose={() => setScaleOpen(false)} doc={doc} settings={settings} />
    </section>
  );
}
