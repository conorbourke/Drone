/**
 * Validation report (docs/phases/PHASE3.md section 5): every case the engine is checked
 * against, grouped by category, with what is compared, the reference value and its source, the
 * engine's value, the error, the tolerance and pass/fail/skipped/info, plus the job status and
 * "Run again". When this server has no report of its own yet, the committed snapshot is shown.
 */
import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import type { ValidationCase, ValidationResponse, ValidationStatus } from '../api/analysis';
import { api, errorMessage, isAbortError, isAuthError } from '../api/client';
import { EmptyState } from '../components/EmptyState';
import { Explain } from '../components/Explain';
import { StatusPill, type PillTone } from '../components/StatusPill';
import { AppShell } from '../layout/AppShell';
import { formatSig } from '../lib/analysis';
import { formatDateTime } from '../lib/format';

const STATUS: Record<ValidationStatus, { label: string; icon: string; tone: PillTone }> = {
  pass: { label: 'Pass', icon: '✓', tone: 'ok' },
  fail: { label: 'Fail', icon: '✕', tone: 'error' },
  skipped: { label: 'Skipped', icon: '–', tone: 'neutral' },
  info: { label: 'Info', icon: 'i', tone: 'info' },
};

function value(v: number | null, unit: string): string {
  if (v === null || !Number.isFinite(v)) return '–';
  const digits = Math.abs(v) >= 100 ? 1 : Math.abs(v) >= 1 ? 3 : 4;
  return `${formatSig(v, digits)}${unit ? ` ${unit}` : ''}`;
}

function CaseRow({ c }: { c: ValidationCase }) {
  const s = STATUS[c.status] ?? STATUS.info;
  return (
    <tr data-testid="validation-case" data-case-id={c.id} data-status={c.status}>
      <td>
        <div className="validation-case-name">{c.name}</div>
        <div className="small muted">{c.compared}</div>
        {c.note ? <div className="small validation-note">{c.note}</div> : null}
      </td>
      <td className="num">
        {value(c.reference.value, c.reference.unit)}
        <div className="small muted validation-source">{c.reference.source}</div>
      </td>
      <td className="num">{value(c.engine.value, c.engine.unit)}</td>
      <td className="num">
        {c.error_pct === null ? '–' : `${c.error_pct >= 0 ? '+' : '−'}${formatSig(Math.abs(c.error_pct), 1)} %`}
      </td>
      <td className="num">{c.tolerance_pct === null ? '–' : `±${formatSig(c.tolerance_pct, 1)} %`}</td>
      <td>
        <StatusPill tone={s.tone} className="status-badge">
          <span aria-hidden="true" className="status-icon">
            {s.icon}
          </span>
          {s.label}
        </StatusPill>
      </td>
    </tr>
  );
}

export function ValidationPage() {
  const [data, setData] = useState<ValidationResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  // Bumped after "Run again" so the polling effect starts over.
  const [pollNonce, setPollNonce] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | null = null;
    const load = async () => {
      try {
        const next = await api<ValidationResponse>('/api/validation', { signal: controller.signal });
        setData(next);
        setLoadError(null);
        if (next.job.status === 'queued' || next.job.status === 'running') timer = window.setTimeout(() => void load(), 1500);
      } catch (caught) {
        if (isAbortError(caught) || isAuthError(caught)) return;
        setLoadError(errorMessage(caught));
      }
    };
    void load();
    return () => {
      controller.abort();
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [pollNonce]);

  const runAgain = async () => {
    setStarting(true);
    setRunError(null);
    try {
      const next = await api<ValidationResponse>('/api/validation/run', { method: 'POST' });
      setData(next);
      setPollNonce((n) => n + 1);
    } catch (caught) {
      if (!isAuthError(caught)) setRunError(errorMessage(caught));
    } finally {
      setStarting(false);
    }
  };

  const report = data?.report ?? null;
  const job = data?.job;
  const active = job?.status === 'queued' || job?.status === 'running';

  return (
    <AppShell title="Validation">
      <div className="page-header">
        <h1>Validation</h1>
        <Link to="/settings" className="button button-ghost button-sm">
          Settings
        </Link>
      </div>

      <section className="card validation-intro" aria-labelledby="validation-about">
        <h2 id="validation-about">What this page shows</h2>
        <p className="small">
          Validation runs the analysis engine on cases where the right answer is known (textbook results, published vortex-lattice
          and wind-tunnel data, and real aircraft with published specifications) and records how far the engine is from each
          reference. It shows how much to trust the numbers, not that they are exact.
        </p>
        <p className="small">
          The textbook and wind-tunnel cases should agree within a few percent. For the published aircraft the error is expected
          to be large, up to about <strong>±30 %</strong>: their geometry, motors and batteries are only partly published, so the
          tool fills the gaps with assumptions. That is normal for a design tool at this stage; the point is to show the size of
          the error honestly. Skipped cases had no reliable published reference; info rows explain a difference rather than test
          it.
        </p>
      </section>

      {loadError ? (
        <EmptyState title="Could not load the validation report" description={loadError} />
      ) : !data ? (
        <div className="skeleton" aria-busy="true" aria-label="Loading the validation report" />
      ) : (
        <>
          <section className="card" aria-labelledby="validation-status" data-testid="validation-job" data-status={job?.status}>
            <div className="card-header">
              <h2 id="validation-status">Report</h2>
              <button
                type="button"
                className="button button-sm"
                data-testid="validation-run"
                disabled={starting || active}
                onClick={() => void runAgain()}
              >
                {active ? 'Running…' : starting ? 'Starting…' : 'Run again'}
              </button>
            </div>
            {report ? (
              <p className="small muted" data-testid="validation-source" data-source={data.source ?? ''}>
                {data.source === 'snapshot'
                  ? `Showing the snapshot committed with this version of the app (generated ${formatDateTime(report.generated_at)}). Run again to produce this server's own report.`
                  : `Generated by this server ${formatDateTime(report.generated_at)}.`}{' '}
                Engine {report.engine_version}.
              </p>
            ) : null}
            {active ? (
              <div className="analysis-progress" role="status" data-testid="validation-progress">
                <div
                  className="progress-track"
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.round((job?.progress ?? 0) * 100)}
                  aria-label="Validation progress"
                >
                  <span className="progress-fill" style={{ width: `${Math.max(3, Math.round((job?.progress ?? 0) * 100))}%` }} />
                </div>
                <p className="small">
                  {job?.status === 'queued' ? 'Waiting for the analysis engine (analyses run first)…' : job?.stage || 'Running…'}
                </p>
              </div>
            ) : job?.status === 'error' ? (
              <p className="form-error" role="alert">
                The last run failed: {job.error}
              </p>
            ) : job?.finished_at ? (
              <p className="small muted">Last run finished {formatDateTime(job.finished_at)}.</p>
            ) : null}
            {runError ? (
              <p className="form-error" role="alert">
                {runError}
              </p>
            ) : null}

            {report ? (
              <>
                <div className="validation-summary" data-testid="validation-summary">
                  {(['pass', 'fail', 'skipped', 'info'] as const).map((k) => (
                    <div
                      key={k}
                      className={`validation-count count-${k}`}
                      data-testid={`validation-count-${k}`}
                      data-value={report.summary[k]}
                    >
                      <span className="validation-count-value">{report.summary[k]}</span>
                      <span className="validation-count-label">
                        <span aria-hidden="true">{STATUS[k].icon}</span> {STATUS[k].label}
                      </span>
                    </div>
                  ))}
                  <div className="validation-count">
                    <span className="validation-count-value">{report.summary.cases}</span>
                    <span className="validation-count-label">cases</span>
                  </div>
                </div>
                <p className="small muted">{report.tolerance_note}</p>
              </>
            ) : (
              <EmptyState
                title="No report yet"
                description="Press Run again to run the validation suite (about half a minute)."
              />
            )}
          </section>

          {report
            ? report.groups.map((g) => {
                const cases = report.cases.filter((c) => c.group === g.key);
                if (!cases.length) return null;
                return (
                  <section
                    key={g.key}
                    className="card"
                    aria-labelledby={`vg-${g.key}`}
                    data-testid="validation-group"
                    data-group={g.key}
                  >
                    <div className="card-header">
                      <h2 id={`vg-${g.key}`}>{g.label}</h2>
                      {g.key === 'published_design' ? (
                        <Explain
                          label="published aircraft"
                          text="Each aircraft is modelled from its published numbers; anything not published is assumed and stated in the note. A ±30 % tolerance is deliberately wide: it shows the size of the error rather than proving accuracy."
                        />
                      ) : null}
                    </div>
                    <div className="table-scroll">
                      <table className="table table-compact validation-table">
                        <thead>
                          <tr>
                            <th scope="col">Case and what is compared</th>
                            <th scope="col" className="num">
                              Reference (source)
                            </th>
                            <th scope="col" className="num">
                              Engine
                            </th>
                            <th scope="col" className="num">
                              Error
                            </th>
                            <th scope="col" className="num">
                              Tolerance
                            </th>
                            <th scope="col">Result</th>
                          </tr>
                        </thead>
                        <tbody>
                          {cases.map((c) => (
                            <CaseRow key={c.id} c={c} />
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </section>
                );
              })
            : null}
        </>
      )}
    </AppShell>
  );
}
