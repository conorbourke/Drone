/**
 * Files tab (Phase 5, docs/phases/PHASE5.md section 4): "Generate files" for the draft or a
 * saved version, progress while the worker builds them, then the result: print pieces by part
 * with a fit tick against the printer envelope and a 3D preview of each piece on the bed, every
 * file grouped by kind (print, CAD, drawings, bill of materials, notes) with what it is and
 * which free program opens it, and "Download all (ZIP)". Past exports are listed underneath and
 * can be shown again or deleted.
 */
import { Component, lazy, Suspense, useCallback, useEffect, useId, useState } from 'react';
import type { ReactNode } from 'react';
import { api, errorMessage, isAbortError, isAuthError } from '../api/client';
import {
  deleteExport,
  fileUrl,
  getPieceMesh,
  isExportActive,
  listExports,
  startExport,
  useExportJob,
  zipUrl,
  type ExportDetail,
  type ExportFile,
  type ExportItem,
  type ExportManifest,
  type ExportPart,
  type ExportPiece,
  type PieceMesh,
} from '../api/exports';
import type { Settings, SettingsResponse } from '../api/types';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Explain } from '../components/Explain';
import { Modal } from '../components/Modal';
import { StatusPill } from '../components/StatusPill';
import { isAnalysisStale } from '../lib/analysis';
import {
  allPiecesFit,
  dimsText,
  exportSourceText,
  exportStatusText,
  fileName,
  groupFiles,
  kindLabel,
  opensWith,
  pieceId,
  piecesToPrint,
  warningText,
} from '../lib/files';
import { formatBytes, formatDateTime, formatEur, formatMassG, formatNumber, pluralize } from '../lib/format';
import { useWorkspace } from '../lib/workspace';
import '../styles/files.css';

const PiecePreview3D = lazy(() => import('../components/PiecePreview3D'));

class PreviewBoundary extends Component<{ children: ReactNode }, { error: boolean }> {
  override state = { error: false };
  static getDerivedStateFromError() {
    return { error: true };
  }
  override render() {
    if (this.state.error) {
      return (
        <div className="model3d model3d-placeholder piece-preview-3d" data-testid="piece-preview-3d">
          <p className="small muted">The 3D preview could not be loaded. Reload the page to try again; the sizes below still apply.</p>
        </div>
      );
    }
    return this.props.children;
  }
}

function envelopeArray(settings: Settings | null): number[] | null {
  const e = settings?.printer?.usable_envelope_mm;
  return e ? [e.x, e.y, e.z] : null;
}

// ---------------------------------------------------------------------------
// Progress and status
// ---------------------------------------------------------------------------

function ExportProgress({ job }: { job: ExportItem }) {
  const pct = Math.round(Math.max(0, Math.min(1, job.progress)) * 100);
  return (
    <div className="analysis-progress" data-testid="files-progress" data-status={job.status} role="status" aria-live="polite">
      <div className="progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label="File generation progress">
        <span className="progress-fill" style={{ width: `${Math.max(3, pct)}%` }} />
      </div>
      <p className="small">
        <span data-testid="files-stage">{job.stage || (job.status === 'queued' ? 'Waiting for the file generator' : 'Working…')}</span>{' '}
        <span className="muted">({pct} %)</span>
        {job.status === 'queued' && job.queue_position !== null ? (
          <span className="muted">
            {' '}
            · {job.queue_position === 0 ? 'next in the queue' : `${job.queue_position} ahead in the queue`}
          </span>
        ) : null}
      </p>
      <p className="small muted">
        Making the 3D models, splitting them for the printer and writing every file usually takes one to a few minutes. You can
        leave this tab; the files keep being made on the server.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Result: summary, print pieces, grouped files
// ---------------------------------------------------------------------------

function SummaryFigures({ job, manifest }: { job: ExportDetail; manifest: ExportManifest }) {
  const summary = job.summary;
  const parts = manifest.parts ?? [];
  const toPrint = parts.reduce((s, p) => s + piecesToPrint(p), 0);
  const fit = summary?.all_pieces_fit ?? allPiecesFit(manifest);
  const envelope = summary?.envelope_mm ?? manifest.printer?.usable_envelope_mm ?? null;
  const plates = summary?.plates ?? manifest.plates?.count ?? null;
  const bom = summary?.bom_totals ?? manifest.bom?.totals ?? null;
  return (
    <dl className="files-figures" data-testid="files-summary">
      <div>
        <dt>
          Printed pieces{' '}
          <Explain
            label="printed pieces"
            text={`Each part larger than the printer is cut into pieces that fit. ${pluralize(parts.length, 'part')} make ${pluralize(
              parts.reduce((s, p) => s + p.pieces.length, 0),
              'different piece',
            )}; counting parts printed more than once (left and right, one per motor), you print ${pluralize(toPrint, 'piece')} in total.`}
          />
        </dt>
        <dd data-testid="files-piece-count" data-value={toPrint}>
          {formatNumber(toPrint)}
        </dd>
      </div>
      <div>
        <dt>
          Fit check{' '}
          <Explain
            label="fit check"
            text={`Every piece is checked against the usable print envelope from Settings (${dimsText(envelope)}) in the orientation it is printed in. A piece that does not fit stops the export.`}
          />
        </dt>
        <dd>
          {fit === true ? (
            <StatusPill tone="ok" testId="files-fit-all">
              ✓ All fit
            </StatusPill>
          ) : fit === false ? (
            <StatusPill tone="error" testId="files-fit-all">
              Some do not fit
            </StatusPill>
          ) : (
            '—'
          )}
        </dd>
      </div>
      {plates !== null ? (
        <div>
          <dt>
            Printer plates{' '}
            <Explain
              label="printer plates"
              text="The combined 3MF (all_pieces.3mf) arranges every printed piece on printer plates of the bed size with 5 mm spacing. Each plate is one print job."
            />
          </dt>
          <dd data-testid="files-plates">{formatNumber(plates)}</dd>
        </div>
      ) : null}
      {bom?.printed_mass_g !== undefined ? (
        <div>
          <dt>
            Filament{' '}
            <Explain
              label="filament mass"
              text="Estimated mass of all printed pieces from their CAD volume: two perimeters plus the infill of each part's print profile, times the filament density. Real prints vary by about 10 %."
            />
          </dt>
          <dd>{formatMassG(bom.printed_mass_g)}</dd>
        </div>
      ) : null}
      {bom?.line_price_eur !== undefined ? (
        <div>
          <dt>
            Bill of materials{' '}
            <Explain
              label="bill of materials total"
              text={`Sum of every priced line in bom.csv: the selected parts at their best listing, tubes, fasteners, filament and consumables.${
                bom.unpriced_rows ? ` ${pluralize(bom.unpriced_rows, 'line has', 'lines have')} no price yet and are not included.` : ''
              }`}
            />
          </dt>
          <dd data-testid="files-bom-total">{formatEur(bom.line_price_eur)}</dd>
        </div>
      ) : null}
      <div>
        <dt>
          Download size{' '}
          <Explain label="download size" text="Total size of all files of this export. The ZIP is a little smaller because most files compress." />
        </dt>
        <dd>{formatBytes(job.total_size_bytes)}</dd>
      </div>
    </dl>
  );
}

function PieceRow({
  piece,
  exportId,
  onPreview,
}: {
  piece: ExportPiece;
  exportId: number;
  onPreview: (piece: ExportPiece) => void;
}) {
  return (
    <li className="files-piece" data-testid="files-piece" data-piece={pieceId(piece.stl)} data-fits={piece.fits ? 'true' : 'false'}>
      <div className="files-piece-name">
        <span className="files-piece-label">{piece.label}</span>
        {piece.fits ? (
          <StatusPill tone="ok" testId="files-piece-fit" title="Fits the printer envelope in its print orientation">
            ✓ Fits
          </StatusPill>
        ) : (
          <StatusPill tone="error" testId="files-piece-fit">
            Too big
          </StatusPill>
        )}
      </div>
      <div className="files-piece-facts small muted">
        <span title="Size in print orientation (width × depth × height)">{dimsText(piece.size_mm, 0)}</span>
        {piece.filament ? <span>{piece.filament}</span> : null}
        {piece.mass_g !== undefined ? <span>≈ {formatMassG(piece.mass_g)}</span> : null}
        {piece.print_time_band ? <span>{piece.print_time_band} to print</span> : null}
      </div>
      <div className="files-piece-actions">
        <button type="button" className="button button-sm" data-testid="files-piece-preview" onClick={() => onPreview(piece)}>
          Preview
        </button>
        <a className="button button-sm button-ghost" href={fileUrl(exportId, piece.stl)} download data-testid="files-piece-stl">
          STL
        </a>
      </div>
    </li>
  );
}

function PrintParts({
  exportId,
  parts,
  envelope,
  onPreview,
}: {
  exportId: number;
  parts: ExportPart[];
  envelope: number[] | null;
  onPreview: (piece: ExportPiece) => void;
}) {
  if (parts.length === 0) return null;
  return (
    <section className="files-section" data-testid="files-print-parts" aria-labelledby="files-print-heading">
      <h3 id="files-print-heading" className="files-section-title">
        <span>Printed parts</span>
        <span className="small muted">envelope {dimsText(envelope)}</span>
      </h3>
      <p className="small muted">
        Each part with its pieces. Sizes are width × depth × height as the piece stands on the bed. Masses and print times are
        estimates from the CAD volume and the part's print profile.
      </p>
      <div className="files-parts">
        {parts.map((part) => (
          <article key={part.key} className="files-part" data-testid="files-part" data-part={part.key}>
            <div className="files-part-head">
              <div className="files-part-ident">
                <div className="files-part-label">{part.label}</div>
                <div className="small muted">
                  {pluralize(part.pieces.length, 'piece')}
                  {part.quantity > 1 ? `, print ${part.quantity} copies (${pluralize(piecesToPrint(part), 'piece')} in all)` : ''}
                  {part.mass_g_each !== undefined ? ` · ≈ ${formatMassG(part.mass_g_each)} each` : ''}
                  {part.filament ? ` · ${part.filament}` : ''}
                </div>
              </div>
              {part.files?.['3mf'] ? (
                <a className="button button-sm" href={fileUrl(exportId, part.files['3mf'])} download data-testid="files-part-3mf">
                  3MF
                </a>
              ) : null}
            </div>
            {part.description ? <p className="small muted files-part-desc">{part.description}</p> : null}
            <ul className="files-pieces">
              {part.pieces.map((pc) => (
                <PieceRow key={pc.stl} piece={pc} exportId={exportId} onPreview={onPreview} />
              ))}
            </ul>
          </article>
        ))}
      </div>
    </section>
  );
}

function FileRow({ file, exportId }: { file: ExportFile; exportId: number }) {
  return (
    <li className="files-file" data-testid="files-file" data-path={file.path} data-kind={file.kind}>
      <div className="files-file-main">
        <a href={fileUrl(exportId, file.path)} download className="files-file-link" data-testid="files-file-link">
          {fileName(file.path)}
        </a>
        <span className="pill pill-neutral files-kind">{kindLabel(file.kind)}</span>
        <span className="small muted files-size">{formatBytes(file.size_bytes)}</span>
      </div>
      <p className="small muted files-opens" data-testid="files-opens-with">
        {opensWith(file)}
      </p>
    </li>
  );
}

function FileGroups({ exportId, files }: { exportId: number; files: ExportFile[] }) {
  const groups = groupFiles(files);
  return (
    <section className="files-section" aria-labelledby="files-all-heading">
      <h3 id="files-all-heading" className="files-section-title">
        <span>All files</span>
        <span className="small muted">{pluralize(files.length, 'file')}</span>
      </h3>
      {groups.map((group) => (
        <details key={group.key} className="files-group" data-testid="files-group" data-group={group.key} open={group.key !== 'print'}>
          <summary>
            <span className="files-group-title">{group.title}</span>
            <span className="small muted">
              {pluralize(group.files.length, 'file')} · {formatBytes(group.sizeBytes)}
            </span>
          </summary>
          <p className="small muted files-group-desc">{group.description}</p>
          <ul className="files-list">
            {group.files.map((f) => (
              <FileRow key={f.path} file={f} exportId={exportId} />
            ))}
          </ul>
        </details>
      ))}
    </section>
  );
}

function ExportResult({
  job,
  stale,
  onPreview,
}: {
  job: ExportDetail;
  stale: boolean;
  onPreview: (piece: ExportPiece) => void;
}) {
  const manifest = job.manifest;
  if (!manifest) return null;
  const warnings = job.summary?.warnings ?? manifest.warnings ?? [];
  const envelope = job.summary?.envelope_mm ?? manifest.printer?.usable_envelope_mm ?? null;
  return (
    <section className="card files-result" data-testid="files-result" data-export={job.id} aria-labelledby="files-result-heading">
      <div className="card-header">
        <h2 id="files-result-heading">Files of {exportSourceText(job)}</h2>
        <span className="spacer" />
        <a className="button button-primary" href={job.zip_url ?? zipUrl(job.id)} download data-testid="files-zip">
          Download all (ZIP)
        </a>
      </div>
      <p className="card-note" data-testid="files-result-info">
        Made {formatDateTime(job.finished_at ?? job.created_at)}
        {job.duration_s !== null && job.duration_s > 0
          ? job.duration_s < 1
            ? ' in under a second'
            : ` in ${formatNumber(job.duration_s, { maxFractionDigits: 0 })} s`
          : ''}
        {job.reused_from_id ? ' (the design was unchanged, so earlier identical files were reused)' : ''}.{' '}
        {job.parts === 'selected'
          ? 'Motor mounts, servo pockets and the bill of materials use the parts chosen on the Parts tab.'
          : 'No parts were chosen on the Parts tab, so mounts, pockets and the bill of materials use generic sizes.'}
        {job.analysis_id !== null
          ? ' The drawings mark the centre of gravity and neutral point from the latest full analysis.'
          : ' Run the full analysis on the Design tab first to get the centre of gravity and neutral point on the drawings.'}
      </p>
      {stale ? (
        <div className="banner banner-warn" role="status" data-testid="files-stale">
          <div>
            <div className="banner-title">The draft has changed since these files were made</div>
            <div className="small">They describe the draft as it was. Generate the files again to match the current design.</div>
          </div>
        </div>
      ) : null}
      <SummaryFigures job={job} manifest={manifest} />
      {warnings.length > 0 ? (
        <div className="banner banner-warn" role="status" data-testid="files-warnings">
          <div>
            <div className="banner-title">{warnings.length === 1 ? 'One thing to check' : `${warnings.length} things to check`}</div>
            <ul className="small">
              {warnings.map((w, i) => (
                <li key={i}>{warningText(w)}</li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}
      <PrintParts exportId={job.id} parts={manifest.parts ?? []} envelope={envelope} onPreview={onPreview} />
      <FileGroups exportId={job.id} files={manifest.files ?? []} />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Piece preview dialog
// ---------------------------------------------------------------------------

function PiecePreviewDialog({ exportId, piece, onClose }: { exportId: number | null; piece: ExportPiece | null; onClose: () => void }) {
  const [mesh, setMesh] = useState<PieceMesh | null>(null);
  const [error, setError] = useState<string | null>(null);
  const id = piece ? pieceId(piece.stl) : null;

  useEffect(() => {
    if (exportId === null || id === null) return;
    const controller = new AbortController();
    getPieceMesh(exportId, id, controller.signal)
      .then((m) => {
        setMesh(m);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (isAbortError(caught) || isAuthError(caught)) return;
        setError(errorMessage(caught));
      });
    return () => controller.abort();
  }, [exportId, id]);

  const current = mesh && mesh.piece_id === id ? mesh : null;
  const env = current?.envelope_mm ?? piece?.envelope_mm ?? null;
  const size = current?.size_mm ?? piece?.size_mm ?? null;
  const orientation = current?.orientation ?? piece?.orientation;

  return (
    <Modal
      open={piece !== null}
      title={piece ? `Preview: ${piece.label}` : 'Preview'}
      onClose={onClose}
      className="modal-wide"
      testId="piece-preview"
      footer={
        <>
          {piece && exportId !== null ? (
            <a className="button" href={fileUrl(exportId, piece.stl)} download>
              Download STL
            </a>
          ) : null}
          <button type="button" className="button button-primary" onClick={onClose} data-testid="piece-preview-close" autoFocus>
            Close
          </button>
        </>
      }
    >
      {piece ? (
        <div className="piece-preview">
          {error && !current ? (
            <p className="form-error" role="alert">
              Could not load the preview: {error}
            </p>
          ) : current ? (
            <PreviewBoundary>
              <Suspense fallback={<div className="model3d model3d-placeholder piece-preview-3d"><p className="small muted">Loading the 3D view…</p></div>}>
                <PiecePreview3D mesh={current} />
              </Suspense>
            </PreviewBoundary>
          ) : (
            <div className="model3d model3d-placeholder piece-preview-3d" aria-busy="true">
              <p className="small muted">Loading the piece…</p>
            </div>
          )}
          <p className="small muted piece-preview-legend">
            <span className="legend-swatch legend-piece" aria-hidden="true" /> the piece as it stands on the bed ·{' '}
            <span className="legend-swatch legend-bed" aria-hidden="true" /> printer bed
            {current?.bed_mm ? ` ${dimsText(current.bed_mm)}` : ''} ·{' '}
            <span className="legend-swatch legend-envelope" aria-hidden="true" /> usable print envelope. Drag to turn, pinch or scroll to zoom.
          </p>
          <dl className="files-figures" data-testid="piece-preview-facts">
            <div>
              <dt>
                Piece size <Explain label="piece size" text="Width × depth × height of the piece in its print orientation, in millimetres." />
              </dt>
              <dd data-testid="piece-preview-size">{dimsText(size, 1)}</dd>
            </div>
            <div>
              <dt>
                Envelope{' '}
                <Explain
                  label="print envelope"
                  text="The usable print volume from Settings (width × depth × height). It is a little smaller than the printer's build volume to leave room for the brim and the purge line."
                />
              </dt>
              <dd data-testid="piece-preview-envelope">{dimsText(env)}</dd>
            </div>
            <div>
              <dt>Fits</dt>
              <dd>
                {(current?.fits ?? piece.fits) ? (
                  <StatusPill tone="ok" testId="piece-preview-fit">
                    ✓ Yes
                  </StatusPill>
                ) : (
                  <StatusPill tone="error" testId="piece-preview-fit">
                    No
                  </StatusPill>
                )}
              </dd>
            </div>
            {piece.filament ? (
              <div>
                <dt>Filament</dt>
                <dd>{piece.filament}</dd>
              </div>
            ) : null}
            {piece.mass_g !== undefined ? (
              <div>
                <dt>
                  Mass <Explain label="piece mass" text="Estimated printed mass from the CAD volume, the wall and infill settings and the filament density." />
                </dt>
                <dd>≈ {formatMassG(piece.mass_g)}</dd>
              </div>
            ) : null}
            {piece.print_time_band ? (
              <div>
                <dt>
                  Print time <Explain label="print time" text="A rough range from the extruded volume and a typical flow rate for this filament; your slicer gives the exact time." />
                </dt>
                <dd>{piece.print_time_band}</dd>
              </div>
            ) : null}
          </dl>
          {orientation?.description ? (
            <p className="small" data-testid="piece-preview-orientation">
              <strong>Orientation:</strong> {orientation.description}
              {orientation.reason ? ` (${orientation.reason})` : ''}.
            </p>
          ) : null}
          {current?.decimated ? (
            <p className="small muted">
              Simplified for the preview ({formatNumber(current.triangles)} of {formatNumber(current.triangles_original)} triangles); the STL
              file has the full detail.
            </p>
          ) : null}
        </div>
      ) : null}
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// The tab
// ---------------------------------------------------------------------------

export function FilesTab() {
  const workspace = useWorkspace();
  const { projectId, versions, flushDraft } = workspace;
  const selectId = useId();
  const [source, setSource] = useState<string>('draft');
  const [exportsList, setExportsList] = useState<ExportItem[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [previewPiece, setPreviewPiece] = useState<ExportPiece | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<ExportItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const reloadList = useCallback(
    (signal?: AbortSignal) =>
      listExports(projectId, signal)
        .then((list) => {
          setExportsList(list);
          setListError(null);
          // Show the newest export when nothing is chosen yet.
          setCurrentId((id) => id ?? list[0]?.id ?? null);
        })
        .catch((caught: unknown) => {
          if (isAbortError(caught) || isAuthError(caught)) return;
          setListError(errorMessage(caught));
        }),
    [projectId],
  );

  useEffect(() => {
    const controller = new AbortController();
    void reloadList(controller.signal);
    return () => controller.abort();
  }, [reloadList]);

  useEffect(() => {
    const controller = new AbortController();
    api<SettingsResponse>('/api/settings', { signal: controller.signal })
      .then((response) => setSettings(response.settings))
      .catch(() => {
        // The envelope line simply stays hidden; the export uses the stored settings anyway.
      });
    return () => controller.abort();
  }, []);

  const { job, error: pollError, missing } = useExportJob(currentId, () => void reloadList());

  // Keep the list entry of the shown job in step with the polled state.
  const listed = exportsList?.map((item) => (job && item.id === job.id ? { ...item, ...job } : item)) ?? null;

  const selectedVersion = source === 'draft' ? null : (versions?.find((v) => String(v.id) === source) ?? null);
  const effectiveSource = selectedVersion ? String(selectedVersion.id) : 'draft';
  const running = isExportActive(job?.status);

  const generate = async () => {
    setStarting(true);
    setStartError(null);
    try {
      if (effectiveSource === 'draft') await flushDraft();
      const started = await startExport(projectId, effectiveSource === 'draft' ? 'draft' : { version_id: Number(effectiveSource) });
      setExportsList((list) => [started, ...(list ?? []).filter((x) => x.id !== started.id)]);
      setCurrentId(started.id);
    } catch (caught) {
      if (!isAuthError(caught)) setStartError(errorMessage(caught));
    } finally {
      setStarting(false);
    }
  };

  const remove = async (item: ExportItem) => {
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteExport(item.id);
      const rest = (exportsList ?? []).filter((x) => x.id !== item.id);
      setExportsList(rest);
      if (currentId === item.id) setCurrentId(rest[0]?.id ?? null);
      setConfirmDelete(null);
    } catch (caught) {
      if (!isAuthError(caught)) setDeleteError(errorMessage(caught));
      setConfirmDelete(null);
    } finally {
      setDeleting(false);
    }
  };

  const envelope = envelopeArray(settings);
  const stale = !!job && job.status === 'done' && isAnalysisStale(job, { status: workspace.draftStatus, savedAt: workspace.draftSavedAt });

  return (
    <div className="stack files-tab" data-testid="files-tab">
      <section className="card" data-testid="files-generate" aria-labelledby="files-heading">
        <div className="card-header">
          <h2 id="files-heading">Files</h2>
        </div>
        <p className="card-note">
          Everything needed to build the prototype: 3D print files split to fit your printer, CAD models, dimensioned drawings, a bill of
          materials and printing notes, made on the server from the design you choose.
        </p>
        {settings ? (
          <p className="small" data-testid="files-envelope">
            Printer: <strong>{settings.printer.name}</strong>, usable print envelope <strong>{dimsText(envelope)}</strong>{' '}
            <Explain
              label="print envelope"
              text="Every printed part bigger than this box is split into pieces that fit, with alignment keys, bonding surfaces and channels for the carbon tubes at each joint. Change the printer in Settings."
            />
          </p>
        ) : null}
        <div className="analysis-controls">
          <label htmlFor={selectId} className="small muted">
            Make files for
          </label>
          <select
            id={selectId}
            className="input analysis-source"
            data-testid="files-source"
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
            data-testid="files-generate-button"
            disabled={starting || running}
            onClick={() => void generate()}
          >
            {starting ? 'Starting…' : running ? 'Making files…' : 'Generate files'}
          </button>
        </div>
        {startError ? (
          <p className="form-error" role="alert" data-testid="files-error">
            Could not start making the files: {startError}
          </p>
        ) : null}

        {job && running ? <ExportProgress job={job} /> : null}
        {pollError && running ? <p className="small muted">Lost contact with the server ({pollError}); retrying…</p> : null}

        {job?.status === 'error' ? (
          <div className="banner banner-error" role="alert" data-testid="files-job-error">
            <div>
              <div className="banner-title">The files could not be made</div>
              <div className="small">{job.error ?? 'Something went wrong. Try again.'}</div>
            </div>
          </div>
        ) : null}
        {missing ? <p className="small muted">That export was deleted.</p> : null}

        {exportsList !== null && exportsList.length === 0 && currentId === null ? (
          <p className="small muted" data-testid="files-empty">
            No files yet. Press Generate files to make them for the draft.
          </p>
        ) : null}
      </section>

      {job?.status === 'done' ? <ExportResult job={job} stale={stale} onPreview={setPreviewPiece} /> : null}

      <section className="card" data-testid="files-history" aria-labelledby="files-history-heading">
        <div className="card-header">
          <h2 id="files-history-heading">Earlier exports</h2>
        </div>
        <p className="card-note">
          Each time files are made they are kept here until you delete them (or the project). An unchanged design reuses the earlier files
          instead of making them again.
        </p>
        {listError ? (
          <p className="form-error" role="alert">
            Could not load the exports: {listError}
          </p>
        ) : null}
        {deleteError ? (
          <p className="form-error" role="alert" data-testid="files-delete-error">
            Could not delete the export: {deleteError}
          </p>
        ) : null}
        {listed === null && !listError ? <p className="loading small">Loading…</p> : null}
        {listed && listed.length === 0 ? <p className="small muted">None yet.</p> : null}
        {listed && listed.length > 0 ? (
          <ul className="files-history">
            {listed.map((item) => {
              const shown = item.id === currentId;
              const tone = item.status === 'done' ? 'ok' : item.status === 'error' ? 'error' : 'info';
              return (
                <li
                  key={item.id}
                  className={`files-history-item${shown ? ' is-current' : ''}`}
                  data-testid="files-export-item"
                  data-id={item.id}
                  data-status={item.status}
                >
                  <div className="files-history-main">
                    <div>
                      <strong>Files of {exportSourceText(item)}</strong>{' '}
                      <span className="small muted">{formatDateTime(item.created_at)}</span>
                    </div>
                    <div className="small">
                      <StatusPill tone={tone} testId="files-export-status">
                        {exportStatusText(item)}
                      </StatusPill>
                      {item.status === 'done' && item.total_size_bytes !== null ? (
                        <span className="muted"> {formatBytes(item.total_size_bytes)}</span>
                      ) : null}
                      {item.status === 'error' && item.error ? <span className="muted"> {item.error}</span> : null}
                    </div>
                  </div>
                  <div className="files-history-actions">
                    {shown ? (
                      <span className="small muted">Shown above</span>
                    ) : (
                      <button type="button" className="button button-sm" data-testid="files-export-show" onClick={() => setCurrentId(item.id)}>
                        Show
                      </button>
                    )}
                    {item.status === 'done' ? (
                      <a className="button button-sm button-ghost" href={item.zip_url ?? zipUrl(item.id)} download>
                        ZIP
                      </a>
                    ) : null}
                    <button
                      type="button"
                      className="button button-sm button-ghost"
                      data-testid="files-export-delete"
                      onClick={() => setConfirmDelete(item)}
                    >
                      Delete
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        ) : null}
      </section>

      <PiecePreviewDialog exportId={job?.id ?? null} piece={previewPiece} onClose={() => setPreviewPiece(null)} />

      <ConfirmDialog
        open={confirmDelete !== null}
        title="Delete these files?"
        message={
          confirmDelete
            ? `The files of ${exportSourceText(confirmDelete)} made ${formatDateTime(confirmDelete.created_at)} are removed from the server${
                isExportActive(confirmDelete.status) ? ' and the job still running is stopped' : ''
              }. The design is not changed; you can make the files again at any time.`
            : ''
        }
        confirmLabel="Delete"
        danger
        busy={deleting}
        onConfirm={() => confirmDelete && void remove(confirmDelete)}
        onCancel={() => setConfirmDelete(null)}
      />
    </div>
  );
}
