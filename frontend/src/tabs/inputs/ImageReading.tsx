/**
 * "Read images with Claude": the reference dimension form, the run button with progress, the
 * cost note and the missing-key message (from /api/image-readings/status, shown before the
 * button is pressed), then the proposal review table. "Apply selected" writes the accepted
 * values into the draft through the draft controller.
 *
 * The server answers the POST with 202 and a reading in status "running"; this component
 * polls GET /api/image-readings/{id} every 2 s until it is ok, refused or error.
 */
import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { ApiError, api, errorMessage, isAbortError, isAuthError } from '../../api/client';
import { metaFor } from '../../api/schema';
import type {
  DraftDocument,
  ImageReading as Reading,
  ReadingAvailability,
  ReadingProposal,
  ReadingReference,
  ReferenceImage,
  SchemaMap,
} from '../../api/types';
import { Explain } from '../../components/Explain';
import { StatusPill } from '../../components/StatusPill';
import { useToast } from '../../components/Toast';
import { getAtPath } from '../../lib/draft';
import { mergeProposal } from '../../lib/proposal';
import { LAYOUT_LABELS } from '../../engine';

export const POLL_INTERVAL_MS = 2000;
const MAX_READING_IMAGES = 4;

const REFERENCE_OPTIONS: { value: ReadingReference['parameter']; label: string }[] = [
  { value: 'wing.span_mm', label: 'Wingspan' },
  { value: 'fuselage.length_mm', label: 'Fuselage length' },
];

function displayValue(value: unknown, unit: string | null | undefined, path: string, schema: SchemaMap): string {
  if (value === undefined || value === null || value === '') return '–';
  if (typeof value === 'string') {
    const option = metaFor(schema, path).enum?.find((o) => o.value === value);
    if (path === 'layout') return LAYOUT_LABELS[value as keyof typeof LAYOUT_LABELS] ?? value;
    return option?.label ?? value;
  }
  if (typeof value === 'number') {
    const rounded = Math.abs(value) >= 100 ? Math.round(value) : Math.round(value * 100) / 100;
    return `${rounded.toLocaleString('en-IE')}${unit ? ` ${unit}` : ''}`;
  }
  return String(value);
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <span className="confidence" title={`Claude's confidence: ${pct} %`}>
      <span className="confidence-track" aria-hidden="true">
        <span className="confidence-fill" style={{ width: `${pct}%` }} />
      </span>
      <span className="confidence-text">{pct} %</span>
    </span>
  );
}

interface Row {
  path: string;
  label: string;
  current: unknown;
  proposed: unknown;
  unit: string | null;
  confidence: number;
  note: string;
  same: boolean;
}

function proposalRows(proposal: ReadingProposal, doc: DraftDocument, schema: SchemaMap): Row[] {
  const rows: Row[] = [
    {
      path: 'layout',
      label: 'Layout',
      current: doc.parameters.layout,
      proposed: proposal.layout,
      unit: null,
      confidence: proposal.layout_confidence,
      note: proposal.layout_reason,
      same: doc.parameters.layout === proposal.layout,
    },
  ];
  // Server order follows the schema; keep schema order for a stable table.
  const order = Object.keys(schema);
  const paths = Object.keys(proposal.parameters).sort((a, b) => {
    const ia = order.indexOf(a);
    const ib = order.indexOf(b);
    return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib);
  });
  for (const path of paths) {
    const p = proposal.parameters[path];
    const current = getAtPath(doc.parameters, path);
    const meta = metaFor(schema, path);
    rows.push({
      path,
      label: meta.label,
      current,
      proposed: p.value,
      unit: p.unit ?? meta.unit,
      confidence: p.confidence,
      note: p.note,
      same: current === p.value,
    });
  }
  return rows;
}

export function ImageReadingCard({
  projectId,
  images,
  doc,
  update,
  schema,
}: {
  projectId: number;
  images: ReferenceImage[] | null;
  doc: DraftDocument;
  update: (path: string, value: unknown) => void;
  schema: SchemaMap;
}) {
  const toast = useToast();
  const [availability, setAvailability] = useState<ReadingAvailability | null>(null);
  const [parameter, setParameter] = useState<ReadingReference['parameter']>('wing.span_mm');
  const [valueText, setValueText] = useState(() => String(doc.parameters.wing.span_mm ?? ''));
  const [reading, setReading] = useState<Reading | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [appliedId, setAppliedId] = useState<number | null>(null);
  const [applyNote, setApplyNote] = useState<{ blocked: string | null; adjustments: string[] } | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const running = reading?.status === 'running';
  const docRef = useRef(doc);
  useEffect(() => {
    docRef.current = doc;
  });

  // Availability (missing key message up front) and the latest reading, once.
  useEffect(() => {
    const controller = new AbortController();
    api<ReadingAvailability>('/api/image-readings/status', { signal: controller.signal })
      .then(setAvailability)
      .catch((error: unknown) => {
        if (!isAbortError(error) && !isAuthError(error)) setAvailability(null);
      });
    api<Reading[]>(`/api/projects/${projectId}/image-readings`, { signal: controller.signal })
      .then((list) => {
        const latest = list[0];
        if (latest) {
          setReading(latest);
          if (latest.proposal) setSelected(defaultSelection(latest.proposal, doc, schema));
        }
      })
      .catch(() => undefined);
    return () => controller.abort();
    // Only on mount: later edits must not reset the selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // Poll while running.
  const readingId = reading?.id;
  useEffect(() => {
    if (!running || readingId === undefined) return;
    let live = true;
    const tick = () => {
      pollTimer.current = setTimeout(() => {
        api<Reading>(`/api/image-readings/${readingId}`)
          .then((next) => {
            if (!live) return;
            setReading(next);
            if (next.status === 'running') tick();
            else if (next.status === 'ok' && next.proposal) {
              setSelected(defaultSelection(next.proposal, docRef.current, schema));
              toast.success('Claude has read the images. Review the proposal below.');
            }
          })
          .catch((error: unknown) => {
            if (!live || isAuthError(error)) return;
            // Transient network trouble: keep polling.
            tick();
          });
      }, POLL_INTERVAL_MS);
    };
    tick();
    const clock = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      live = false;
      if (pollTimer.current) clearTimeout(pollTimer.current);
      clearInterval(clock);
    };
  }, [running, readingId, schema, toast]);

  const imageCount = images?.length ?? 0;
  const value = Number(valueText);
  const valueValid = valueText.trim() !== '' && Number.isFinite(value) && value > 0;
  const unavailable = availability && !availability.available ? availability.message : null;
  const canRun = !running && !starting && !unavailable && imageCount > 0 && valueValid;

  const run = async (event: FormEvent) => {
    event.preventDefault();
    if (!canRun) return;
    setStarting(true);
    setRunError(null);
    try {
      const ids = (images ?? []).slice(0, MAX_READING_IMAGES).map((i) => i.id);
      const started = await api<Reading>(`/api/projects/${projectId}/image-readings`, {
        method: 'POST',
        body: { reference: { parameter, value_mm: value }, image_ids: ids },
      });
      setReading(started);
      setNow(Date.now());
      setAppliedId(null);
      setApplyNote(null);
    } catch (error) {
      if (isAuthError(error)) return;
      if (error instanceof ApiError && error.status === 503) {
        setAvailability((a) => ({ available: false, model: a?.model ?? '', message: error.detail }));
      }
      setRunError(errorMessage(error));
    } finally {
      setStarting(false);
    }
  };

  const proposal = reading?.status === 'ok' ? reading.proposal : null;
  const rows = proposal ? proposalRows(proposal, doc, schema) : [];

  const apply = () => {
    if (!proposal) return;
    const selections = rows.filter((row) => selected.has(row.path)).map((row) => ({ path: row.path, value: row.proposed }));
    // The selected values with the rest of the draft must still pass the server's checks, or
    // every autosave after this would be refused.
    const merge = mergeProposal(doc.parameters, selections, schema);
    if (merge.blocked) {
      setApplyNote({ blocked: merge.blocked, adjustments: [] });
      return;
    }
    for (const [path, value] of merge.updates) update(`parameters.${path}`, value);
    setApplyNote(merge.adjustments.length > 0 ? { blocked: null, adjustments: merge.adjustments } : null);
    setAppliedId(reading?.id ?? null);
    const count = selections.length;
    toast.success(count === 0 ? 'Nothing selected to apply.' : `Applied ${count} ${count === 1 ? 'value' : 'values'} to the draft.`);
  };

  const elapsedS = reading && running ? Math.max(0, Math.round((now - Date.parse(reading.created_at)) / 1000)) : 0;
  const statusText = (() => {
    if (starting) return 'Sending the images…';
    if (!reading) return null;
    switch (reading.status) {
      case 'running':
        return `Claude is reading ${reading.image_ids.length} ${reading.image_ids.length === 1 ? 'image' : 'images'}… ${elapsedS} s (usually 20–60 s)`;
      case 'ok':
        return 'Proposal ready';
      case 'refused':
        return reading.error ?? 'Claude declined to read these images.';
      case 'error':
        return reading.error ?? 'The reading failed.';
    }
  })();
  const statusTone = !reading || running || starting ? 'info' : reading.status === 'ok' ? 'ok' : reading.status === 'refused' ? 'warn' : 'error';

  return (
    <section className="card" aria-labelledby="reading-heading" data-testid="reading-card">
      <div className="card-header">
        <h2 id="reading-heading">Read images with Claude</h2>
      </div>
      <p className="card-note">
        Claude looks at the images, picks the nearest supported layout and estimates the proportions. One real
        dimension turns the proportions into millimetres. You review every value before anything changes.
      </p>

      {unavailable ? (
        <div className="banner banner-warn" role="status" data-testid="reading-unavailable">
          <div>
            <div className="banner-title">Image reading is not enabled yet</div>
            <div className="small">{unavailable}</div>
          </div>
        </div>
      ) : null}

      <form className="reading-form" onSubmit={(event) => void run(event)}>
        <div className="field">
          <div className="field-head">
            <label htmlFor="reading-parameter" className="field-label">
              Known dimension
            </label>
            <Explain
              label="known dimension"
              text="Pick a dimension you know for real (from the render's description or your own target). Everything else is scaled from it."
            />
          </div>
          <div className="field-control">
            <select
              id="reading-parameter"
              data-testid="reading-reference-parameter"
              value={parameter}
              onChange={(event) => {
                const next = event.target.value as ReadingReference['parameter'];
                setParameter(next);
                const current = getAtPath(doc.parameters, next);
                if (typeof current === 'number') setValueText(String(current));
              }}
            >
              {REFERENCE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="field">
          <div className="field-head">
            <label htmlFor="reading-value" className="field-label">
              Real size
            </label>
          </div>
          <div className="field-control">
            <input
              id="reading-value"
              data-testid="reading-reference-value"
              type="number"
              inputMode="decimal"
              min={1}
              step="any"
              value={valueText}
              aria-invalid={!valueValid || undefined}
              onChange={(event) => setValueText(event.target.value)}
            />
            <span className="field-unit">mm</span>
          </div>
        </div>
        <div className="reading-actions">
          <button type="submit" className="button button-primary" data-testid="reading-run" disabled={!canRun}>
            {running || starting ? 'Reading…' : 'Read images with Claude'}
          </button>
        </div>
      </form>
      <p className="small muted reading-cost">
        One reading costs roughly a few cents of Claude API usage.
        {imageCount > MAX_READING_IMAGES ? ` The first ${MAX_READING_IMAGES} images are read.` : ''}
        {imageCount === 0 ? ' Upload at least one image first.' : ''}
      </p>

      {statusText ? (
        <div className="reading-status-row">
          <StatusPill tone={statusTone} testId="reading-status">
            {statusText}
          </StatusPill>
          {running || starting ? <span className="progress-indeterminate" aria-hidden="true" /> : null}
        </div>
      ) : null}
      {runError && !unavailable ? (
        <p className="form-error" role="alert">
          {runError}
        </p>
      ) : null}

      {proposal ? (
        <div className="proposal" data-testid="proposal">
          <h3 className="proposal-title">Proposal</h3>
          <div className="layout-suggestion" data-testid="proposal-layout">
            <strong>Suggested layout: {LAYOUT_LABELS[proposal.layout] ?? proposal.layout}</strong>{' '}
            <span className="muted">({Math.round(proposal.layout_confidence * 100)} % confident)</span>
            <p className="small">{proposal.layout_reason}</p>
          </div>
          <div className="row proposal-toolbar">
            <button type="button" className="button button-sm" data-testid="proposal-select-all" onClick={() => setSelected(new Set(rows.map((r) => r.path)))}>
              Select all
            </button>
            <button type="button" className="button button-sm" data-testid="proposal-select-none" onClick={() => setSelected(new Set())}>
              Select none
            </button>
            <span className="spacer" />
            <span className="small muted">
              {selected.size} of {rows.length} selected
            </span>
          </div>
          <div className="table-scroll">
            <table className="table proposal-table">
              <thead>
                <tr>
                  <th scope="col">Use</th>
                  <th scope="col">Parameter</th>
                  <th scope="col" className="num">
                    Current
                  </th>
                  <th scope="col" className="num">
                    Proposed
                  </th>
                  <th scope="col">Confidence</th>
                  <th scope="col">Note</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.path} data-testid="proposal-row" data-path={row.path} className={row.same ? 'is-same' : undefined}>
                    <td>
                      <input
                        type="checkbox"
                        data-testid="proposal-accept"
                        aria-label={`Use the proposed ${row.label}`}
                        checked={selected.has(row.path)}
                        onChange={(event) => {
                          const next = new Set(selected);
                          if (event.target.checked) next.add(row.path);
                          else next.delete(row.path);
                          setSelected(next);
                        }}
                      />
                    </td>
                    <td>{row.label}</td>
                    <td className="num muted">{displayValue(row.current, row.unit, row.path, schema)}</td>
                    <td className="num">
                      <strong>{displayValue(row.proposed, row.unit, row.path, schema)}</strong>
                    </td>
                    <td>
                      <ConfidenceBar value={row.confidence} />
                    </td>
                    <td className="small proposal-note">{row.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {proposal.warnings.length > 0 ? (
            <div className="banner banner-warn" role="status">
              <div>
                <div className="banner-title">Check these before applying</div>
                <ul className="small">
                  {proposal.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            </div>
          ) : null}
          {proposal.unmapped_notes.length > 0 ? (
            <details className="small">
              <summary>What Claude could not use ({proposal.unmapped_notes.length})</summary>
              <ul>
                {proposal.unmapped_notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            </details>
          ) : null}
          <div className="row proposal-apply-row">
            <button type="button" className="button button-primary" data-testid="proposal-apply" disabled={selected.size === 0} onClick={apply}>
              Apply selected
            </button>
            {appliedId === reading?.id ? (
              <span className="small muted" data-testid="proposal-applied">
                Applied. Undo by restoring a saved version.
              </span>
            ) : (
              <span className="small muted">Values you do not select keep their current setting.</span>
            )}
          </div>
          {applyNote?.blocked ? (
            <p className="banner banner-warn small" role="alert" data-testid="proposal-blocked">
              {applyNote.blocked}
            </p>
          ) : null}
          {applyNote && !applyNote.blocked && applyNote.adjustments.length > 0 && appliedId === reading?.id ? (
            <div className="banner banner-warn small" role="status" data-testid="proposal-adjusted">
              <div>
                <div className="banner-title">Adjusted so the design stays valid</div>
                <ul>
                  {applyNote.adjustments.map((a, i) => (
                    <li key={i}>{a}</li>
                  ))}
                </ul>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

/** Rows selected by default: values that would change and that Claude is reasonably sure of. */
function defaultSelection(proposal: ReadingProposal, doc: DraftDocument, schema: SchemaMap): Set<string> {
  return new Set(proposalRows(proposal, doc, schema).filter((r) => !r.same && r.confidence >= 0.5).map((r) => r.path));
}
