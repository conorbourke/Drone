/**
 * Reference images: drag-and-drop or button upload (JPEG, PNG, WebP; up to 15 MB each and six
 * per project), a view selector per image, thumbnails from the stored file, and delete.
 */
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { ChangeEvent, DragEvent } from 'react';
import { api, apiUpload, errorMessage, isAbortError, isAuthError } from '../../api/client';
import type { ImageView, ReferenceImage } from '../../api/types';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Explain } from '../../components/Explain';
import { useToast } from '../../components/Toast';
import { formatBytes } from '../../lib/format';

export const VIEW_OPTIONS: { value: ImageView; label: string }[] = [
  { value: 'top', label: 'Top' },
  { value: 'side', label: 'Side' },
  { value: 'front', label: 'Front' },
  { value: 'three_quarter', label: 'Three-quarter' },
  { value: 'other', label: 'Other' },
];

export const MAX_IMAGES = 6;
const ACCEPT = 'image/jpeg,image/png,image/webp';

/** Suggest a view for a new image: from the file name, else the first standard view not used yet. */
export function guessView(filename: string, used: ImageView[]): ImageView {
  const name = filename.toLowerCase();
  if (/three[-_ ]?quarter|3[-_ ]?4|perspective|iso/.test(name)) return 'three_quarter';
  if (/\btop\b|plan|above/.test(name.replace(/[-_.]/g, ' '))) return 'top';
  if (/\bside\b|profile/.test(name.replace(/[-_.]/g, ' '))) return 'side';
  if (/\bfront\b/.test(name.replace(/[-_.]/g, ' '))) return 'front';
  return (['top', 'side', 'front', 'three_quarter'] as ImageView[]).find((v) => !used.includes(v)) ?? 'other';
}

export function useProjectImages(projectId: number) {
  const [images, setImages] = useState<ReferenceImage[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const reload = useCallback(
    (signal?: AbortSignal) =>
      api<ReferenceImage[]>(`/api/projects/${projectId}/images`, { signal })
        .then((list) => {
          setImages(list);
          setLoadError(null);
        })
        .catch((error: unknown) => {
          if (isAbortError(error) || isAuthError(error)) return;
          setLoadError(errorMessage(error));
        }),
    [projectId],
  );
  useEffect(() => {
    const controller = new AbortController();
    void reload(controller.signal);
    return () => controller.abort();
  }, [reload]);
  return { images, setImages, loadError, reload };
}

export function ReferenceImages({
  projectId,
  images,
  setImages,
  loadError,
  reload,
}: {
  projectId: number;
  images: ReferenceImage[] | null;
  setImages: (updater: (list: ReferenceImage[] | null) => ReferenceImage[] | null) => void;
  loadError: string | null;
  reload: () => Promise<void>;
}) {
  const toast = useToast();
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [confirmDelete, setConfirmDelete] = useState<ReferenceImage | null>(null);
  const count = images?.length ?? 0;

  const upload = async (files: File[]) => {
    if (files.length === 0) return;
    const room = MAX_IMAGES - count;
    const messages: string[] = [];
    if (files.length > room) {
      messages.push(`A project holds at most ${MAX_IMAGES} images; ${files.length - Math.max(room, 0)} not uploaded.`);
    }
    const used = (images ?? []).map((i) => i.view);
    setUploading((n) => n + Math.max(0, Math.min(files.length, room)));
    for (const file of files.slice(0, Math.max(room, 0))) {
      const view = guessView(file.name, used);
      used.push(view);
      const form = new FormData();
      form.append('file', file);
      form.append('view', view);
      try {
        const created = await apiUpload<ReferenceImage>(`/api/projects/${projectId}/images`, form);
        setImages((list) => [...(list ?? []), created]);
      } catch (error) {
        if (!isAuthError(error)) messages.push(`${file.name}: ${errorMessage(error)}`);
      } finally {
        setUploading((n) => n - 1);
      }
    }
    setErrors(messages);
  };

  const onFiles = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    event.target.value = '';
    void upload(files);
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragOver(false);
    void upload(Array.from(event.dataTransfer.files ?? []));
  };

  const changeView = async (image: ReferenceImage, view: ImageView) => {
    setImages((list) => list?.map((i) => (i.id === image.id ? { ...i, view } : i)) ?? null);
    try {
      await api<ReferenceImage>(`/api/images/${image.id}`, { method: 'PATCH', body: { view } });
    } catch (error) {
      if (!isAuthError(error)) toast.error(`Could not change the view: ${errorMessage(error)}`);
      void reload();
    }
  };

  const remove = async (image: ReferenceImage) => {
    try {
      await api<void>(`/api/images/${image.id}`, { method: 'DELETE' });
      setImages((list) => list?.filter((i) => i.id !== image.id) ?? null);
      setConfirmDelete(null);
    } catch (error) {
      if (!isAuthError(error)) toast.error(`Could not delete the image: ${errorMessage(error)}`);
    }
  };

  return (
    <section className="card" aria-labelledby="images-heading" data-testid="images-card">
      <div className="card-header">
        <h2 id="images-heading">Reference images</h2>
        <span className="small muted">
          {count} of {MAX_IMAGES}
        </span>
      </div>
      <p className="card-note">
        Upload three or four renders of the aircraft from different angles (top, side, front, three-quarter) and set
        which view each one shows. JPEG, PNG or WebP, up to 15 MB each.
      </p>

      <div
        className={`dropzone${dragOver ? ' is-over' : ''}`}
        data-testid="image-dropzone"
        onDragOver={(event) => {
          event.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
      >
        <p className="dropzone-text">Drop images here, or</p>
        <label htmlFor={inputId} className={`button button-primary${count >= MAX_IMAGES ? ' is-disabled' : ''}`}>
          Choose images
        </label>
        <input
          ref={inputRef}
          id={inputId}
          className="visually-hidden"
          type="file"
          accept={ACCEPT}
          multiple
          data-testid="image-upload"
          disabled={count >= MAX_IMAGES}
          onChange={onFiles}
        />
        {uploading > 0 ? (
          <p className="small muted" role="status">
            Uploading {uploading} {uploading === 1 ? 'image' : 'images'}…
          </p>
        ) : null}
      </div>

      {errors.length > 0 ? (
        <ul className="form-error upload-errors" role="alert">
          {errors.map((m, i) => (
            <li key={i}>{m}</li>
          ))}
        </ul>
      ) : null}
      {loadError ? (
        <p className="form-error">
          Could not load the images: {loadError}{' '}
          <button type="button" className="button button-sm" onClick={() => void reload()}>
            Retry
          </button>
        </p>
      ) : null}

      {images && images.length > 0 ? (
        <ul className="image-grid" aria-label="Uploaded reference images">
          {images.map((image) => (
            <li key={image.id} className="image-item" data-testid="image-item" data-image-id={image.id}>
              <img src={image.url} alt={`${image.filename}, ${image.view.replace('_', '-')} view`} loading="lazy" />
              <div className="image-meta">
                <span className="image-name" title={image.filename}>
                  {image.filename}
                </span>
                <span className="faint small">
                  {image.width_px} × {image.height_px} px · {formatBytes(image.size_bytes)}
                </span>
              </div>
              <div className="image-actions">
                <label className="visually-hidden" htmlFor={`view-${image.id}`}>
                  View shown in {image.filename}
                </label>
                <select
                  id={`view-${image.id}`}
                  data-testid="image-view"
                  value={image.view}
                  onChange={(event) => void changeView(image, event.target.value as ImageView)}
                >
                  {VIEW_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="button button-sm button-ghost"
                  data-testid="image-delete"
                  aria-label={`Delete ${image.filename}`}
                  onClick={() => setConfirmDelete(image)}
                >
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      ) : images ? (
        <p className="small muted">
          No images yet.{' '}
          <Explain
            label="reference images"
            text="AI-generated renders work well. Claude reads proportions from them, not sizes, so one real dimension (below) scales everything."
          />
        </p>
      ) : null}

      <ConfirmDialog
        open={confirmDelete !== null}
        title="Delete image?"
        message={confirmDelete ? `${confirmDelete.filename} will be deleted. Readings already made are kept.` : ''}
        confirmLabel="Delete"
        danger
        onConfirm={() => {
          if (confirmDelete) void remove(confirmDelete);
        }}
        onCancel={() => setConfirmDelete(null)}
      />
    </section>
  );
}
