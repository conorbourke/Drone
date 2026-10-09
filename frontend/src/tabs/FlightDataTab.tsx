/**
 * Flight data tab (Phase 6): how to set up ArduPilot logging, upload a log (or load the
 * bundled simulator flight), follow it being read, and see its phases, charts and the
 * predicted-versus-measured comparison; calibration factors with Apply and Undo; and the
 * built (weighed) component masses.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage, isAbortError, isAuthError } from '../api/client';
import {
  deleteFlightLog,
  getFlightLog,
  getFlightSeries,
  getGuide,
  listFlightLogs,
  loadSampleFlight,
  reprocessFlightLog,
  updateFlightLog,
  uploadFlightLog,
  type FlightLogDetail,
  type FlightLogItem,
  type FlightSeries,
  type LoggingGuide,
} from '../api/flightData';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Explain } from '../components/Explain';
import { StatusPill } from '../components/StatusPill';
import { useToast } from '../components/Toast';
import { formatBytes, formatDateTime } from '../lib/format';
import { formatClock } from '../lib/flightData';
import { useWorkspace } from '../lib/workspace';
import '../styles/flight.css';
import { BuiltWeightsForm } from './flight/BuiltWeightsForm';
import { CalibrationPanel } from './flight/CalibrationPanel';
import { FlightResult } from './flight/FlightResult';
import { LoggingGuideCard } from './flight/LoggingGuide';

const POLL_MS = 1500;

/** The selected log's detail and chart series, tagged with the log state they belong to. */
interface LoadedView {
  key: string;
  detail: FlightLogDetail | null;
  detailError: string | null;
  series: FlightSeries | null;
  seriesError: string | null;
}

const EMPTY_VIEW: LoadedView = { key: '', detail: null, detailError: null, series: null, seriesError: null };

function statusTone(status: FlightLogItem['status']): 'ok' | 'info' | 'error' | 'neutral' {
  return status === 'done' ? 'ok' : status === 'error' ? 'error' : 'info';
}

function statusText(item: FlightLogItem): string {
  if (item.status === 'done') return 'Read';
  if (item.status === 'error') return 'Failed';
  if (item.status === 'queued') return 'Waiting';
  return `Reading ${Math.round(item.progress * 100)} %`;
}

export function FlightDataTab() {
  const { projectId, versions } = useWorkspace();
  const toast = useToast();
  const [guide, setGuide] = useState<LoggingGuide | null>(null);
  const [guideError, setGuideError] = useState<string | null>(null);
  const [logs, setLogs] = useState<FlightLogItem[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [view, setView] = useState<LoadedView>(EMPTY_VIEW);
  const [upload, setUpload] = useState<{ name: string; fraction: number } | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [versionChoice, setVersionChoice] = useState<string>('draft');
  const [massText, setMassText] = useState('');
  const [sampleBusy, setSampleBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<FlightLogItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [calKey, setCalKey] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const uploadAbort = useRef<AbortController | null>(null);

  useEffect(() => {
    getGuide()
      .then(setGuide)
      .catch((e: unknown) => {
        if (!isAuthError(e)) setGuideError(errorMessage(e));
      });
    return () => uploadAbort.current?.abort();
  }, []);

  const reload = useCallback(
    (signal?: AbortSignal) =>
      listFlightLogs(projectId, signal)
        .then((list) => {
          setLogs(list);
          setListError(null);
          setSelectedId((cur) => (cur !== null && list.some((l) => l.id === cur) ? cur : (list[0]?.id ?? null)));
        })
        .catch((e: unknown) => {
          if (isAbortError(e) || isAuthError(e)) return;
          setListError(errorMessage(e));
        }),
    [projectId],
  );

  useEffect(() => {
    const controller = new AbortController();
    void reload(controller.signal);
    return () => controller.abort();
  }, [reload]);

  // Poll while any log is waiting or being read.
  const pending = logs?.some((l) => l.status === 'queued' || l.status === 'running') ?? false;
  useEffect(() => {
    if (!pending) return;
    const timer = window.setInterval(() => void reload(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [pending, reload]);

  const selected = logs?.find((l) => l.id === selectedId) ?? null;
  const selectedKey = selected ? `${selected.id}:${selected.status}:${selected.finished_at ?? ''}` : '';

  // Load the detail and the chart series of the selected log once it is read.
  useEffect(() => {
    const [idText, status] = selectedKey.split(':');
    if (status !== 'done') return;
    const id = Number(idText);
    const key = selectedKey;
    const patch = (values: Partial<LoadedView>) =>
      setView((v) => ({ ...(v.key === key ? v : { ...EMPTY_VIEW, key }), ...values }));
    const controller = new AbortController();
    getFlightLog(id, controller.signal)
      .then((detail) => {
        patch({ detail });
        setCalKey((k) => k + 1);
      })
      .catch((e: unknown) => {
        if (!isAbortError(e) && !isAuthError(e)) patch({ detailError: errorMessage(e) });
      });
    getFlightSeries(id, 600, controller.signal)
      .then((series) => patch({ series }))
      .catch((e: unknown) => {
        if (!isAbortError(e) && !isAuthError(e)) patch({ seriesError: errorMessage(e) });
      });
    return () => controller.abort();
  }, [selectedKey]);
  const shown = view.key === selectedKey ? view : EMPTY_VIEW;

  const parseMass = (): number | null | undefined => {
    const t = massText.trim().replace(',', '.');
    if (!t) return null;
    const n = Number(t);
    return Number.isFinite(n) && n > 0 && n <= 30 ? n : undefined;
  };

  const onFile = (file: File | undefined) => {
    if (!file) return;
    setUploadError(null);
    const mass = parseMass();
    if (mass === undefined) {
      setUploadError('Enter the take-off mass in kg (between 0 and 30), or leave it blank.');
      return;
    }
    if (guide && file.size > guide.max_upload_mb * 1024 * 1024) {
      setUploadError(`The file is ${formatBytes(file.size)}; the limit is ${guide.max_upload_mb} MB per log.`);
      return;
    }
    const controller = new AbortController();
    uploadAbort.current = controller;
    setUpload({ name: file.name, fraction: 0 });
    uploadFlightLog(projectId, file, {
      versionId: versionChoice === 'draft' ? null : Number(versionChoice),
      takeoffMassKg: mass,
      onProgress: (fraction) => setUpload({ name: file.name, fraction }),
      signal: controller.signal,
    })
      .then((item) => {
        setSelectedId(item.id);
        toast.success(`${item.filename} uploaded. Reading it now.`);
        return reload();
      })
      .catch((e: unknown) => {
        if (isAbortError(e) || isAuthError(e)) return;
        setUploadError(errorMessage(e));
      })
      .finally(() => {
        setUpload(null);
        uploadAbort.current = null;
        if (fileInput.current) fileInput.current.value = '';
      });
  };

  const onSample = () => {
    setSampleBusy(true);
    loadSampleFlight(projectId)
      .then((item) => {
        setSelectedId(item.id);
        return reload();
      })
      .catch((e: unknown) => {
        if (!isAuthError(e)) toast.error(errorMessage(e));
      })
      .finally(() => setSampleBusy(false));
  };

  const onDelete = () => {
    if (!confirmDelete) return;
    setDeleting(true);
    deleteFlightLog(confirmDelete.id)
      .then(() => {
        toast.success(`${confirmDelete.filename} deleted.`);
        setConfirmDelete(null);
        setCalKey((k) => k + 1);
        return reload();
      })
      .catch((e: unknown) => {
        if (!isAuthError(e)) toast.error(errorMessage(e));
      })
      .finally(() => setDeleting(false));
  };

  const onRecompare = (item: FlightLogItem) => {
    reprocessFlightLog(item.id)
      .then(() => reload())
      .catch((e: unknown) => {
        if (!isAuthError(e)) toast.error(errorMessage(e));
      });
  };

  const onMassChange = (item: FlightLogItem, text: string) => {
    const t = text.trim().replace(',', '.');
    const value = t ? Number(t) : null;
    if (value !== null && !(Number.isFinite(value) && value > 0 && value <= 30)) {
      toast.error('Enter the take-off mass in kg (between 0 and 30), or leave it blank.');
      return;
    }
    if (value === item.takeoff_mass_kg) return;
    updateFlightLog(item.id, { takeoff_mass_kg: value })
      .then(() => reload())
      .catch((e: unknown) => {
        if (!isAuthError(e)) toast.error(errorMessage(e));
      });
  };

  return (
    <div className="flight-tab" data-testid="flight-tab">
      <LoggingGuideCard guide={guide} error={guideError} />

      <section className="card" data-testid="flight-upload" aria-labelledby="flight-upload-heading">
        <div className="card-header">
          <h2 id="flight-upload-heading">Add a flight log</h2>
        </div>
        <p className="small card-note">
          Upload the DataFlash log of one flight (<code>.bin</code>, at most {guide?.max_upload_mb ?? 200} MB). It is read on the
          server and compared with this design’s predictions.
        </p>
        <div className="flight-upload-grid">
          <label className="field">
            <span className="field-label">
              Flown with
              <Explain label="flown with" text="The saved version (or the current draft) the aircraft was built to. Its predictions are the ones compared. A version with flight logs cannot be deleted." />
            </span>
            <span className="field-control">
              <select value={versionChoice} onChange={(e) => setVersionChoice(e.target.value)} data-testid="flight-upload-version">
                <option value="draft">Current draft</option>
                {(versions ?? []).map((v) => (
                  <option key={v.id} value={String(v.id)}>
                    Version {v.number}: {v.name}
                  </option>
                ))}
              </select>
            </span>
          </label>
          <label className="field">
            <span className="field-label">
              Take-off mass (optional)
              <Explain label="take-off mass" text="The aircraft weighed ready to fly, battery and camera fitted. Without it the predicted mass is used, and any mass error then shows up as a power error." />
            </span>
            <span className="field-control">
              <input
                inputMode="decimal"
                value={massText}
                onChange={(e) => setMassText(e.target.value)}
                placeholder="e.g. 3.2"
                data-testid="flight-upload-mass"
              />
              <span className="field-unit">kg</span>
            </span>
          </label>
        </div>
        <div className="flight-actions">
          <label className={`button button-primary${upload ? ' is-disabled' : ''}`}>
            Choose a log file…
            <input
              ref={fileInput}
              type="file"
              accept=".bin,.BIN,.log,.LOG"
              className="visually-hidden"
              disabled={!!upload}
              data-testid="flight-upload-input"
              onChange={(e) => onFile(e.target.files?.[0])}
            />
          </label>
          {guide?.sample_available !== false ? (
            <button type="button" className="button" onClick={onSample} disabled={sampleBusy} data-testid="flight-load-sample">
              {sampleBusy ? 'Loading…' : 'Load sample flight'}
            </button>
          ) : null}
          <Explain
            label="sample flight"
            text="A real ArduPilot log from ArduPilot’s own simulator (a 4.5 kg QuadPlane flying take-off hover, transition, a cruise loop, back-transition and landing). It shows how the comparison works; it is a different aircraft, so the numbers do not judge this design."
          />
        </div>
        {upload ? (
          <div className="flight-progress" data-testid="flight-upload-progress">
            <span className="small">
              Uploading {upload.name}: {Math.round(upload.fraction * 100)} %
            </span>
            <progress max={1} value={upload.fraction} aria-label={`Upload of ${upload.name}`} />
            <button type="button" className="button button-sm" onClick={() => uploadAbort.current?.abort()}>
              Cancel
            </button>
          </div>
        ) : null}
        {uploadError ? (
          <p className="small field-error" role="alert" data-testid="flight-upload-error">
            {uploadError}
          </p>
        ) : null}
      </section>

      <section className="card" data-testid="flight-logs" aria-labelledby="flight-logs-heading">
        <div className="card-header">
          <h2 id="flight-logs-heading">Flight logs</h2>
        </div>
        {listError ? <p className="small field-error">{listError}</p> : null}
        {logs && logs.length === 0 ? (
          <p className="small muted" data-testid="flight-logs-empty">
            No flight logs yet. Upload one, or load the sample flight to see how it works.
          </p>
        ) : null}
        <ul className="flight-log-list">
          {(logs ?? []).map((l) => (
            <li
              key={l.id}
              className={`flight-log-item${l.id === selectedId ? ' is-selected' : ''}`}
              data-testid="flight-log-item"
              data-log-id={l.id}
              data-status={l.status}
            >
              <button type="button" className="flight-log-select" onClick={() => setSelectedId(l.id)} aria-pressed={l.id === selectedId}>
                <span className="flight-log-name">
                  {l.filename}
                  {l.sample ? <span className="faint"> · sample</span> : null}
                </span>
                <span className="small muted">
                  {l.source === 'version' ? `Version ${l.version_number}` : 'Draft'} · {formatBytes(l.size_bytes)} ·{' '}
                  {l.flight_duration_s ? `${formatClock(l.flight_duration_s)} flight` : formatDateTime(l.created_at)}
                  {l.counts ? ` · ${l.counts.inside} inside, ${l.counts.outside} outside` : ''}
                </span>
              </button>
              <StatusPill tone={statusTone(l.status)} testId="flight-log-status">
                {statusText(l)}
              </StatusPill>
              <div className="flight-log-actions">
                <label className="flight-log-mass small">
                  <span>Mass</span>
                  <input
                    key={`${l.id}-${l.takeoff_mass_kg ?? ''}`}
                    inputMode="decimal"
                    defaultValue={l.takeoff_mass_kg ?? ''}
                    aria-label={`Take-off mass of ${l.filename} in kg`}
                    disabled={l.status === 'queued' || l.status === 'running'}
                    onBlur={(e) => onMassChange(l, e.target.value)}
                  />
                  <span>kg</span>
                </label>
                <button
                  type="button"
                  className="button button-sm"
                  onClick={() => onRecompare(l)}
                  disabled={l.status === 'queued' || l.status === 'running'}
                  title="Read the log and compare it again (for example after a new analysis)"
                >
                  Compare again
                </button>
                <button type="button" className="button button-sm button-danger" onClick={() => setConfirmDelete(l)} data-testid="flight-log-delete">
                  Delete
                </button>
              </div>
              {l.status === 'error' && l.error ? <p className="small field-error flight-log-error">{l.error}</p> : null}
              {(l.status === 'queued' || l.status === 'running') && l.stage ? (
                <p className="small muted flight-log-error">
                  {l.stage}
                  {l.queue_position ? ` (${l.queue_position} job${l.queue_position === 1 ? '' : 's'} ahead)` : ''}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      </section>

      {shown.detail && selected && shown.detail.id === selected.id ? (
        <FlightResult log={shown.detail} series={shown.series} seriesError={shown.seriesError} />
      ) : null}
      {shown.detailError ? <p className="small field-error">{shown.detailError}</p> : null}

      <CalibrationPanel projectId={projectId} refreshKey={calKey} />
      <BuiltWeightsForm projectId={projectId} onSaved={() => setCalKey((k) => k + 1)} />

      <ConfirmDialog
        open={confirmDelete !== null}
        title="Delete this flight log?"
        message={`${confirmDelete?.filename ?? ''} and everything read from it will be removed. An applied calibration keeps its factors until you apply or undo it again.`}
        confirmLabel="Delete log"
        danger
        busy={deleting}
        onConfirm={onDelete}
        onCancel={() => setConfirmDelete(null)}
      />
    </div>
  );
}
