/**
 * Moulds (Phase 7, docs/phases/PHASE7.md section 1): choose the carbon parts, generate their
 * two-part female moulds on the server with progress, then per part the halves and their tiles
 * (fit, filament, time, print orientation, a preview on the printer bed), the draft check with
 * flagged faces explained, the parting line, the assembly order and print notes; every file
 * grouped by type with what opens it, and a ZIP. Earlier mould sets are listed underneath.
 */
import { Component, lazy, Suspense, useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { errorMessage, isAbortError, isAuthError } from '../../api/client';
import type { ExportSource } from '../../api/exports';
import {
  deleteMoulds,
  getTileMesh,
  isMouldActive,
  listMoulds,
  MOULD_PARTS,
  mouldFileUrl,
  mouldZipUrl,
  startMoulds,
  useMouldJob,
  type MouldDetail,
  type MouldHalf,
  type MouldItem,
  type MouldManifest,
  type MouldPart,
  type MouldPartKey,
  type MouldTile,
  type TileMesh,
} from '../../api/moulds';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Explain } from '../../components/Explain';
import { Modal } from '../../components/Modal';
import { StatusPill } from '../../components/StatusPill';
import { isAnalysisStale } from '../../lib/analysis';
import { dimsText, fileName, kindLabel } from '../../lib/files';
import { formatBytes, formatDateTime, formatMassG, formatNumber, pluralize } from '../../lib/format';
import {
  degText,
  draftDots,
  draftSummaryText,
  FLAGGED_ADVICE,
  flaggedFaceText,
  groupMouldFiles,
  mouldPartsText,
  mouldStatusText,
  partMassG,
  partTiles,
  tileId,
} from '../../lib/moulds';
import { useWorkspace } from '../../lib/workspace';

const PiecePreview3D = lazy(() => import('../../components/PiecePreview3D'));

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

const DRAFT_CHOICES = [1, 1.5, 2, 3, 5];

function sourceLabel(item: Pick<MouldItem, 'source' | 'version_number'>): string {
  return item.source === 'version' ? `v${item.version_number ?? '?'}` : 'the draft';
}

// ---------------------------------------------------------------------------
// Progress
// ---------------------------------------------------------------------------

function MouldProgress({ job }: { job: MouldItem }) {
  const pct = Math.round(Math.max(0, Math.min(1, job.progress)) * 100);
  return (
    <div className="analysis-progress" data-testid="moulds-progress" data-status={job.status} role="status" aria-live="polite">
      <div className="progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label="Mould generation progress">
        <span className="progress-fill" style={{ width: `${Math.max(3, pct)}%` }} />
      </div>
      <p className="small">
        <span data-testid="moulds-stage">{job.stage || (job.status === 'queued' ? 'Waiting for the mould generator' : 'Working…')}</span>{' '}
        <span className="muted">({pct} %)</span>
        {job.status === 'queued' && job.queue_position !== null ? (
          <span className="muted"> · {job.queue_position === 0 ? 'next in the queue' : `${job.queue_position} ahead in the queue`}</span>
        ) : null}
      </p>
      <p className="small muted">
        Moulds are heavy work: the nose, fuselage and fairing of a 24&#8239;kg design take several minutes, up to about half an hour on the
        server. You can leave this tab; the moulds keep being made.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Result
// ---------------------------------------------------------------------------

function DraftMapView({ part, half }: { part: MouldPart; half: MouldHalf }) {
  const map = part.draft.map;
  if (!map) return null;
  const width = 300;
  const height = 110;
  const dots = draftDots(map, half.key, part.draft.min_draft_deg, width, height);
  if (dots.length === 0) return null;
  return (
    <figure className="draft-map">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Draft map of the ${half.key} half`} preserveAspectRatio="xMidYMid meet">
        <rect x="0" y="0" width={width} height={height} className="draft-map-bg" />
        {dots.map((d, i) => (
          <circle key={i} cx={d.x} cy={d.y} r="1.6" className={`draft-dot draft-${d.bin}`} />
        ))}
      </svg>
      <figcaption className="small muted">{half.label}: seen along its pull direction, nose to the left.</figcaption>
    </figure>
  );
}

function TileRow({ tile, mouldId, onPreview }: { tile: MouldTile; mouldId: number; onPreview: (t: MouldTile) => void }) {
  const stl = tile.files.stl;
  return (
    <li className="files-piece mould-tile" data-testid="moulds-tile" data-tile={stl ? tileId(stl) : tile.label} data-fits={tile.fits ? 'true' : 'false'}>
      <div className="files-piece-name">
        <span className="files-piece-label">{tile.label}</span>
        {tile.fits ? (
          <StatusPill tone="ok" title="Fits the printer envelope in its print orientation">
            ✓ Fits
          </StatusPill>
        ) : (
          <StatusPill tone="error">Too big</StatusPill>
        )}
      </div>
      <div className="files-piece-facts small muted">
        <span title="Size in print orientation (width × depth × height)">{dimsText(tile.size_mm, 0)}</span>
        {tile.filament ? <span>{tile.filament}</span> : null}
        {tile.estimated_mass_g !== undefined ? <span>≈ {formatMassG(tile.estimated_mass_g)}</span> : null}
        {tile.estimated_print_time ? <span>{tile.estimated_print_time} to print</span> : null}
        {tile.print_orientation?.name ? <span>Print {tile.print_orientation.name}</span> : null}
      </div>
      <div className="files-piece-actions">
        {stl ? (
          <>
            <button type="button" className="button button-sm" data-testid="moulds-tile-preview" onClick={() => onPreview(tile)}>
              Preview
            </button>
            <a className="button button-sm button-ghost" href={mouldFileUrl(mouldId, stl)} download>
              STL
            </a>
          </>
        ) : null}
      </div>
    </li>
  );
}

function PartView({ part, mouldId, onPreview }: { part: MouldPart; mouldId: number; onPreview: (t: MouldTile) => void }) {
  const draft = part.draft;
  const flagged = draft.flagged_detail ?? [];
  const mould = part.mould;
  return (
    <article className="files-part mould-part" data-testid="moulds-part" data-part={part.key}>
      <div className="files-part-head">
        <div className="files-part-ident">
          <div className="files-part-label">{part.label}</div>
          <div className="small muted">
            {pluralize(part.halves.length, 'half', 'halves')}, {pluralize(partTiles(part), 'tile')} · ≈ {formatMassG(partMassG(part))} of{' '}
            {part.print_notes?.filament ?? 'filament'}
          </div>
        </div>
        {part.files?.pdf ? (
          <a className="button button-sm" href={mouldFileUrl(mouldId, part.files.pdf)} download data-testid="moulds-part-pdf">
            Sheet (PDF)
          </a>
        ) : null}
      </div>
      {part.description ? <p className="small muted files-part-desc">{part.description}</p> : null}

      <div className="mould-draft" data-testid="moulds-draft" data-flagged={draft.flagged_faces.length}>
        <h4 className="fs-sub">
          Draft check{' '}
          <Explain
            label="draft"
            text={`Draft is the angle a mould face leans away from the direction the mould half is lifted off. With at least ${degText(draft.min_draft_deg)} the part slides out; walls closer to straight can grip the laminate. The strip right next to the parting line is always near 0° on a rounded part and is not counted.`}
          />{' '}
          {draft.flagged_faces.length === 0 ? (
            <StatusPill tone="ok">✓ Enough draft</StatusPill>
          ) : (
            <StatusPill tone="warn" testId="moulds-flagged">
              {pluralize(draft.flagged_faces.length, 'face', 'faces')} flagged
            </StatusPill>
          )}
        </h4>
        <p className="small" data-testid="moulds-draft-text">
          {draftSummaryText(draft)}
        </p>
        {flagged.length ? (
          <>
            <ul className="small mould-flagged">
              {flagged.map((f) => (
                <li key={f.face}>{flaggedFaceText(f, draft.min_draft_deg)}</li>
              ))}
            </ul>
            <p className="small muted">{FLAGGED_ADVICE}</p>
          </>
        ) : null}
        {draft.map ? (
          <>
            <div className="draft-maps">
              {part.halves.map((h) => (
                <DraftMapView key={h.key} part={part} half={h} />
              ))}
            </div>
            <p className="small muted draft-legend">
              <span className="legend-swatch draft-ok" aria-hidden="true" /> {degText(2 * draft.min_draft_deg)} or more ·{' '}
              <span className="legend-swatch draft-low" aria-hidden="true" /> {degText(draft.min_draft_deg)} to {degText(2 * draft.min_draft_deg)} ·{' '}
              <span className="legend-swatch draft-flag" aria-hidden="true" /> under {degText(draft.min_draft_deg)} (flagged) ·{' '}
              <span className="legend-swatch draft-band" aria-hidden="true" /> parting-line strip (not counted)
            </p>
          </>
        ) : null}
      </div>

      {part.parting_line?.description ? (
        <p className="small">
          <strong>Parting line:</strong> {part.parting_line.description}{' '}
          <Explain
            label="parting line"
            text="Where the two mould halves meet. It is placed at the widest outline of the part seen along the pull direction, so each half can be lifted straight off."
          />
        </p>
      ) : null}

      {part.halves.map((half) => (
        <div key={half.key} className="mould-half" data-testid="moulds-half" data-half={half.key}>
          <div className="mould-half-head">
            <strong>{half.label}</strong>
            {half.demould?.demouldable === false ? (
              <StatusPill tone="error">Undercut: will not release</StatusPill>
            ) : (
              <StatusPill tone="ok" title="Rays cast along the pull direction found no undercut">
                ✓ Releases
              </StatusPill>
            )}
            {half.step_file ? (
              <a className="button button-sm button-ghost" href={mouldFileUrl(mouldId, half.step_file)} download>
                STEP
              </a>
            ) : null}
          </div>
          {half.pull_note ? <p className="small muted">{half.pull_note}</p> : null}
          <ul className="files-pieces">
            {half.tiles.map((t) => (
              <TileRow key={t.label} tile={t} mouldId={mouldId} onPreview={onPreview} />
            ))}
          </ul>
        </div>
      ))}

      {mould ? (
        <p className="small muted" data-testid="moulds-construction">
          {mould.wall_mm !== undefined ? `Mould wall ${formatNumber(mould.wall_mm)} mm` : ''}
          {mould.flange_width_mm !== undefined ? `, ${formatNumber(mould.flange_width_mm)} mm flange` : ''}
          {mould.bolt ? ` with ${mould.bolt} bolt holes` : ''}
          {mould.bolt_pitch_mm !== undefined ? ` every ${formatNumber(mould.bolt_pitch_mm)} mm` : ''}
          {mould.registration_keys?.type ? `; registration keys: ${mould.registration_keys.type}` : ''}
          {mould.trim_line?.offset_mm !== undefined ? `; trim line scribed ${formatNumber(mould.trim_line.offset_mm)} mm outside the finished edge` : ''}.
          {mould.laminate_allowance
            ? ` The mould is the outside of the part; the laminate (${mould.laminate_allowance.layup ?? 'carbon'}, about ${formatNumber(
                mould.laminate_allowance.laminate_mm ?? 0,
              )} mm) builds up inward.`
            : ''}
        </p>
      ) : null}

      <details className="breakdown" data-testid="moulds-assembly">
        <summary>Assembly order and print notes</summary>
        {part.assembly_order?.length ? (
          <ol className="small mould-steps">
            {part.assembly_order.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        ) : null}
        {part.print_notes?.finishing?.length ? (
          <>
            <h5 className="fs-sub">Finishing the mould face</h5>
            <ul className="small">
              {part.print_notes.finishing.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          </>
        ) : null}
        {part.notes?.length ? (
          <>
            <h5 className="fs-sub">Notes</h5>
            <ul className="small">
              {part.notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          </>
        ) : null}
      </details>
    </article>
  );
}

function MouldDownloads({ mouldId, manifest }: { mouldId: number; manifest: MouldManifest }) {
  const groups = groupMouldFiles(manifest.files ?? []);
  const labels = new Map(manifest.parts.map((p) => [p.key, p.label]));
  return (
    <section className="files-section" aria-labelledby="moulds-files-heading">
      <h3 id="moulds-files-heading" className="files-section-title">
        <span>All mould files</span>
        <span className="small muted">{pluralize(manifest.files?.length ?? 0, 'file')}</span>
      </h3>
      {groups.map((group) => (
        <details key={group.key} className="files-group" data-testid="moulds-group" data-group={group.key} open={group.key === 'pdf' || group.key === 'step'}>
          <summary>
            <span className="files-group-title">{group.title}</span>
            <span className="small muted">
              {pluralize(group.files.length, 'file')} · {formatBytes(group.sizeBytes)}
            </span>
          </summary>
          <p className="small muted files-group-desc">{group.description}</p>
          <p className="small files-group-desc" data-testid="moulds-opens-with">
            <strong>Opens with:</strong> {group.opensWith}
          </p>
          <ul className="files-list">
            {group.files.map((f) => (
              <li key={f.path} className="files-file" data-testid="moulds-file" data-path={f.path} data-kind={f.kind}>
                <div className="files-file-main">
                  <a href={mouldFileUrl(mouldId, f.path)} download className="files-file-link">
                    {fileName(f.path)}
                  </a>
                  <span className="pill pill-neutral files-kind">{kindLabel(f.kind)}</span>
                  <span className="small muted files-size">{formatBytes(f.size_bytes)}</span>
                </div>
                <p className="small muted files-opens">{labels.get(f.part) ?? f.part}</p>
              </li>
            ))}
          </ul>
        </details>
      ))}
    </section>
  );
}

function MouldResult({ job, stale, onPreview }: { job: MouldDetail; stale: boolean; onPreview: (t: MouldTile) => void }) {
  const manifest = job.manifest;
  if (!manifest) return null;
  const summary = job.summary;
  return (
    <section className="card files-result" data-testid="moulds-result" data-mould={job.id} aria-labelledby="moulds-result-heading">
      <div className="card-header">
        <h2 id="moulds-result-heading">Moulds of {sourceLabel(job)}</h2>
        <span className="spacer" />
        <a className="button button-primary" href={job.zip_url ?? mouldZipUrl(job.id)} download data-testid="moulds-zip">
          Download all (ZIP)
        </a>
      </div>
      <p className="card-note">
        Made {formatDateTime(job.finished_at ?? job.created_at)}
        {job.duration_s !== null && job.duration_s >= 1 ? ` in ${formatNumber(job.duration_s / 60, { maxFractionDigits: 1 })} min` : ''}
        {job.reused_from_id ? ' (the design was unchanged, so earlier identical moulds were reused)' : ''} for the {mouldPartsText(job.mould_parts)}.
        Minimum draft {degText(job.options.min_draft_deg ?? 2)}.
      </p>
      {stale ? (
        <div className="banner banner-warn" role="status" data-testid="moulds-stale">
          <div>
            <div className="banner-title">The draft has changed since these moulds were made</div>
            <div className="small">They match the draft as it was. Generate them again to match the current design.</div>
          </div>
        </div>
      ) : null}
      {summary ? (
        <dl className="files-figures" data-testid="moulds-summary">
          <div>
            <dt>
              Tiles <Explain label="tiles" text="Mould halves bigger than the printer are cut into tiles that fit, bolted and keyed together on the back." />
            </dt>
            <dd data-testid="moulds-tile-count" data-value={summary.tiles}>
              {formatNumber(summary.tiles)}
            </dd>
          </div>
          <div>
            <dt>
              Fit check <Explain label="fit check" text={`Every tile is checked against the usable print envelope (${dimsText(summary.envelope_mm)}) in its print orientation.`} />
            </dt>
            <dd>
              {summary.all_tiles_fit ? (
                <StatusPill tone="ok" testId="moulds-fit-all">
                  ✓ All fit
                </StatusPill>
              ) : (
                <StatusPill tone="error" testId="moulds-fit-all">
                  Some do not fit
                </StatusPill>
              )}
            </dd>
          </div>
          <div>
            <dt>
              Release <Explain label="release check" text="Rays cast from the part surface along each half's pull direction must not hit the part again; any hit is an undercut that would lock the part in the mould." />
            </dt>
            <dd>{summary.demouldable ? <StatusPill tone="ok">✓ Every half</StatusPill> : <StatusPill tone="error">Undercut found</StatusPill>}</dd>
          </div>
          <div>
            <dt>
              Flagged faces <Explain label="flagged faces" text="Faces with less than the minimum draft away from the parting line. Explained per part below." />
            </dt>
            <dd data-testid="moulds-flagged-count">{formatNumber(summary.flagged_faces)}</dd>
          </div>
          <div>
            <dt>
              Filament <Explain label="filament mass" text="Estimated mass of all printed tiles from their volume and the print settings (8 perimeters, 25 % infill)." />
            </dt>
            <dd>≈ {formatMassG(summary.estimated_mass_g)}</dd>
          </div>
          <div>
            <dt>
              Download size <Explain label="download size" text="Total size of all files of this mould set." />
            </dt>
            <dd>{formatBytes(job.total_size_bytes)}</dd>
          </div>
        </dl>
      ) : null}
      {manifest.notes?.length ? (
        <ul className="small fs-notes">
          {manifest.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      ) : null}
      <section className="files-section" aria-label="Moulds by part">
        <h3 className="files-section-title">
          <span>Moulds by part</span>
          <span className="small muted">envelope {dimsText(summary?.envelope_mm ?? manifest.envelope_mm)}</span>
        </h3>
        <div className="files-parts">
          {manifest.parts.map((p) => (
            <PartView key={p.key} part={p} mouldId={job.id} onPreview={onPreview} />
          ))}
        </div>
      </section>
      <MouldDownloads mouldId={job.id} manifest={manifest} />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Tile preview
// ---------------------------------------------------------------------------

function TilePreviewDialog({ mouldId, tile, onClose }: { mouldId: number | null; tile: MouldTile | null; onClose: () => void }) {
  const [mesh, setMesh] = useState<TileMesh | null>(null);
  const [error, setError] = useState<string | null>(null);
  const id = tile?.files.stl ? tileId(tile.files.stl) : null;

  useEffect(() => {
    if (mouldId === null || id === null) return;
    const controller = new AbortController();
    getTileMesh(mouldId, id, controller.signal)
      .then((m) => {
        setMesh(m);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (isAbortError(caught) || isAuthError(caught)) return;
        setError(errorMessage(caught));
      });
    return () => controller.abort();
  }, [mouldId, id]);

  const current = mesh && mesh.piece_id === id ? mesh : null;
  return (
    <Modal
      open={tile !== null}
      title={tile ? `Preview: tile ${tile.label}` : 'Preview'}
      onClose={onClose}
      className="modal-wide"
      testId="tile-preview"
      footer={
        <>
          {tile?.files.stl && mouldId !== null ? (
            <a className="button" href={mouldFileUrl(mouldId, tile.files.stl)} download>
              Download STL
            </a>
          ) : null}
          <button type="button" className="button button-primary" onClick={onClose} data-testid="tile-preview-close" autoFocus>
            Close
          </button>
        </>
      }
    >
      {tile ? (
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
              <p className="small muted">Loading the tile…</p>
            </div>
          )}
          <p className="small muted piece-preview-legend">
            <span className="legend-swatch legend-piece" aria-hidden="true" /> the tile as it stands on the bed ·{' '}
            <span className="legend-swatch legend-bed" aria-hidden="true" /> printer bed{current?.bed_mm ? ` ${dimsText(current.bed_mm)}` : ''} ·{' '}
            <span className="legend-swatch legend-envelope" aria-hidden="true" /> usable print envelope. Drag to turn, pinch or scroll to zoom.
          </p>
          <dl className="files-figures" data-testid="tile-preview-facts">
            <div>
              <dt>
                Tile size <Explain label="tile size" text="Width × depth × height of the tile in its print orientation, in millimetres." />
              </dt>
              <dd data-testid="tile-preview-size">{dimsText(current?.size_mm ?? tile.size_mm, 1)}</dd>
            </div>
            <div>
              <dt>Fits</dt>
              <dd>
                {tile.fits ? (
                  <StatusPill tone="ok" testId="tile-preview-fit">
                    ✓ Yes
                  </StatusPill>
                ) : (
                  <StatusPill tone="error" testId="tile-preview-fit">
                    No
                  </StatusPill>
                )}
              </dd>
            </div>
            {tile.filament ? (
              <div>
                <dt>Filament</dt>
                <dd>{tile.filament}</dd>
              </div>
            ) : null}
            {tile.estimated_mass_g !== undefined ? (
              <div>
                <dt>
                  Mass <Explain label="tile mass" text="Estimated printed mass from the tile volume and the print settings." />
                </dt>
                <dd>≈ {formatMassG(tile.estimated_mass_g)}</dd>
              </div>
            ) : null}
            {tile.estimated_print_time ? (
              <div>
                <dt>
                  Print time <Explain label="print time" text="A rough range from the extruded volume and a typical flow rate; your slicer gives the exact time." />
                </dt>
                <dd>{tile.estimated_print_time}</dd>
              </div>
            ) : null}
          </dl>
          {tile.print_orientation?.name ? (
            <p className="small" data-testid="tile-preview-orientation">
              <strong>Orientation:</strong> {tile.print_orientation.name}
              {tile.print_orientation.reason ? `. ${tile.print_orientation.reason}` : ''}
              {tile.print_orientation.supports ? ` Supports: ${tile.print_orientation.supports}.` : ''}
            </p>
          ) : null}
          {tile.notes?.length ? (
            <ul className="small">
              {tile.notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Section
// ---------------------------------------------------------------------------

export function MouldsSection({ source, sourceText }: { source: ExportSource; sourceText: string }) {
  const workspace = useWorkspace();
  const { projectId, flushDraft } = workspace;
  const [parts, setParts] = useState<MouldPartKey[]>(['nose', 'fuselage', 'wing_root_fairing']);
  const [minDraft, setMinDraft] = useState(2);
  const [list, setList] = useState<MouldItem[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [previewTile, setPreviewTile] = useState<MouldTile | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<MouldItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const reloadList = useCallback(
    (signal?: AbortSignal) =>
      listMoulds(projectId, signal)
        .then((items) => {
          setList(items);
          setListError(null);
          setCurrentId((id) => id ?? items[0]?.id ?? null);
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

  const { job, error: pollError, missing } = useMouldJob(currentId, () => void reloadList());
  const listed = list?.map((item) => (job && item.id === job.id ? { ...item, ...job } : item)) ?? null;
  const running = isMouldActive(job?.status);

  const toggle = (key: MouldPartKey) =>
    setParts((current) => (current.includes(key) ? current.filter((p) => p !== key) : MOULD_PARTS.map((p) => p.key).filter((k) => k === key || current.includes(k))));

  const generate = async () => {
    setStarting(true);
    setStartError(null);
    try {
      if (source === 'draft') await flushDraft();
      const started = await startMoulds(projectId, { source, parts, min_draft_deg: minDraft });
      setList((items) => [started, ...(items ?? []).filter((x) => x.id !== started.id)]);
      setCurrentId(started.id);
    } catch (caught) {
      if (!isAuthError(caught)) setStartError(errorMessage(caught));
    } finally {
      setStarting(false);
    }
  };

  const remove = async (item: MouldItem) => {
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteMoulds(item.id);
      const rest = (list ?? []).filter((x) => x.id !== item.id);
      setList(rest);
      if (currentId === item.id) setCurrentId(rest[0]?.id ?? null);
      setConfirmDelete(null);
    } catch (caught) {
      if (!isAuthError(caught)) setDeleteError(errorMessage(caught));
      setConfirmDelete(null);
    } finally {
      setDeleting(false);
    }
  };

  const stale = !!job && job.status === 'done' && isAnalysisStale(job, { status: workspace.draftStatus, savedAt: workspace.draftSavedAt });

  return (
    <>
      <section className="card" data-testid="moulds-card" aria-labelledby="moulds-heading">
        <div className="card-header">
          <h2 id="moulds-heading">Moulds for the carbon parts</h2>
        </div>
        <p className="card-note">
          Two-part female moulds you 3D print, finish and then lay carbon into: one mould half for each side of the part, split into tiles that
          fit the printer, with bolting flanges, registration keys and a trim line. Made from the outside shape of {sourceText}.
        </p>
        <fieldset className="mould-parts" data-testid="moulds-parts">
          <legend className="small muted">Make moulds for</legend>
          {MOULD_PARTS.map((p) => (
            <label key={p.key} className="mould-part-option">
              <input type="checkbox" checked={parts.includes(p.key)} onChange={() => toggle(p.key)} data-testid={`moulds-part-${p.key}`} />
              <span>
                <strong>{p.label}</strong>
                <span className="small muted"> {p.description}</span>
              </span>
            </label>
          ))}
        </fieldset>
        <div className="analysis-controls">
          <label className="small muted" htmlFor="moulds-min-draft">
            Minimum draft{' '}
            <Explain
              label="minimum draft"
              text="Faces of the part that lean less than this away from the pull direction are flagged in the report (the part shape is never changed). 2° is the usual rule for composite moulds."
            />
          </label>
          <select id="moulds-min-draft" className="input mould-draft-select" data-testid="moulds-min-draft" value={minDraft} onChange={(e) => setMinDraft(Number(e.target.value))}>
            {DRAFT_CHOICES.map((d) => (
              <option key={d} value={d}>
                {degText(d)}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="button button-primary"
            data-testid="moulds-generate"
            disabled={starting || running || parts.length === 0}
            onClick={() => void generate()}
          >
            {starting ? 'Starting…' : running ? 'Making moulds…' : 'Generate moulds'}
          </button>
        </div>
        {parts.length === 0 ? <p className="small muted">Choose at least one part.</p> : null}
        {startError ? (
          <p className="form-error" role="alert" data-testid="moulds-error">
            Could not start making the moulds: {startError}
          </p>
        ) : null}
        {job && running ? <MouldProgress job={job} /> : null}
        {pollError && running ? <p className="small muted">Lost contact with the server ({pollError}); retrying…</p> : null}
        {job?.status === 'error' ? (
          <div className="banner banner-error" role="alert" data-testid="moulds-job-error">
            <div>
              <div className="banner-title">The moulds could not be made</div>
              <div className="small">{job.error ?? 'Something went wrong. Try again.'}</div>
            </div>
          </div>
        ) : null}
        {missing ? <p className="small muted">That mould set was deleted.</p> : null}
        {list !== null && list.length === 0 && currentId === null ? (
          <p className="small muted" data-testid="moulds-empty">
            No moulds yet. Choose the parts and press Generate moulds.
          </p>
        ) : null}
      </section>

      {job?.status === 'done' ? <MouldResult job={job} stale={stale} onPreview={setPreviewTile} /> : null}

      <section className="card" data-testid="moulds-history" aria-labelledby="moulds-history-heading">
        <div className="card-header">
          <h2 id="moulds-history-heading">Earlier mould sets</h2>
        </div>
        <p className="card-note">Kept until you delete them (or the project). An unchanged design and part choice reuses the earlier set.</p>
        {listError ? (
          <p className="form-error" role="alert">
            Could not load the mould sets: {listError}
          </p>
        ) : null}
        {deleteError ? (
          <p className="form-error" role="alert">
            Could not delete the mould set: {deleteError}
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
                <li key={item.id} className={`files-history-item${shown ? ' is-current' : ''}`} data-testid="moulds-item" data-id={item.id} data-status={item.status}>
                  <div className="files-history-main">
                    <div>
                      <strong>
                        Moulds of {sourceLabel(item)}: {mouldPartsText(item.mould_parts)}
                      </strong>{' '}
                      <span className="small muted">{formatDateTime(item.created_at)}</span>
                    </div>
                    <div className="small">
                      <StatusPill tone={tone} testId="moulds-item-status">
                        {mouldStatusText(item)}
                      </StatusPill>
                      {item.status === 'done' && item.total_size_bytes !== null ? <span className="muted"> {formatBytes(item.total_size_bytes)}</span> : null}
                      {item.status === 'error' && item.error ? <span className="muted"> {item.error}</span> : null}
                    </div>
                  </div>
                  <div className="files-history-actions">
                    {shown ? (
                      <span className="small muted">Shown above</span>
                    ) : (
                      <button type="button" className="button button-sm" data-testid="moulds-item-show" onClick={() => setCurrentId(item.id)}>
                        Show
                      </button>
                    )}
                    {item.status === 'done' ? (
                      <a className="button button-sm button-ghost" href={item.zip_url ?? mouldZipUrl(item.id)} download>
                        ZIP
                      </a>
                    ) : null}
                    <button type="button" className="button button-sm button-ghost" data-testid="moulds-item-delete" onClick={() => setConfirmDelete(item)}>
                      Delete
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        ) : null}
      </section>

      <TilePreviewDialog mouldId={job?.id ?? null} tile={previewTile} onClose={() => setPreviewTile(null)} />

      <ConfirmDialog
        open={confirmDelete !== null}
        title="Delete this mould set?"
        message={
          confirmDelete
            ? `The mould files of ${sourceLabel(confirmDelete)} made ${formatDateTime(confirmDelete.created_at)} are removed from the server${
                isMouldActive(confirmDelete.status) ? ' and the job still running is stopped' : ''
              }. The design is not changed; you can make the moulds again at any time.`
            : ''
        }
        confirmLabel="Delete"
        danger
        busy={deleting}
        onConfirm={() => confirmDelete && void remove(confirmDelete)}
        onCancel={() => setConfirmDelete(null)}
      />
    </>
  );
}
