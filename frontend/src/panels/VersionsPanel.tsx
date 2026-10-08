/**
 * Right-rail Versions panel: save the draft as a version, list versions, and restore,
 * duplicate, rename or delete them. Also shows which version the draft is based on.
 */
import { useState } from 'react';
import type { FormEvent } from 'react';
import { ApiError, api, errorMessage, isAuthError } from '../api/client';
import type { DesignVersion, Draft, VersionSummary } from '../api/types';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { EmptyState } from '../components/EmptyState';
import { Explain } from '../components/Explain';
import { Menu } from '../components/Menu';
import { Modal } from '../components/Modal';
import { StatusPill } from '../components/StatusPill';
import { useToast } from '../components/Toast';
import { formatDateTime } from '../lib/format';
import type { DraftController } from '../lib/useDraft';

interface VersionsPanelProps {
  projectId: number;
  /** null while loading. */
  versions: VersionSummary[] | null;
  loadError: string | null;
  onReload: () => Promise<void>;
  draft: DraftController;
  /** Text for the draft-basis indicator. */
  basisLabel: string;
  /** True when restoring would discard edits that no version holds. */
  dirty: boolean;
  onVersionSaved: (version: DesignVersion) => void;
  onRestored: (draft: Draft, version: VersionSummary) => void;
  onVersionDeleted: (versionId: number) => void;
}

type Dialog =
  | { kind: 'none' }
  | { kind: 'save' }
  | { kind: 'rename'; version: VersionSummary }
  | { kind: 'restore'; version: VersionSummary }
  | { kind: 'delete'; version: VersionSummary };

export function VersionsPanel({
  projectId,
  versions,
  loadError,
  onReload,
  draft,
  basisLabel,
  dirty,
  onVersionSaved,
  onRestored,
  onVersionDeleted,
}: VersionsPanelProps) {
  const toast = useToast();
  const [dialog, setDialog] = useState<Dialog>({ kind: 'none' });
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [notes, setNotes] = useState('');

  const closeDialog = () => {
    if (busy) return;
    setDialog({ kind: 'none' });
    setFormError(null);
  };

  const openSave = () => {
    const nextNumber = versions && versions.length ? Math.max(...versions.map((v) => v.number)) + 1 : 1;
    setName(`v${nextNumber}`);
    setNotes('');
    setFormError(null);
    setDialog({ kind: 'save' });
  };

  const openRename = (version: VersionSummary) => {
    setName(version.name);
    setNotes(version.notes ?? '');
    setFormError(null);
    setDialog({ kind: 'rename', version });
  };

  const fail = (error: unknown, fallback: string) => {
    if (isAuthError(error)) return;
    toast.error(`${fallback}: ${errorMessage(error)}`);
  };

  const submitSave = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setFormError('Give the version a name.');
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      const flushed = await draft.flush();
      if (!flushed) {
        setFormError(
          draft.errorMessage
            ? `The draft could not be saved (${draft.errorMessage}). Fix that first, then save the version.`
            : 'The draft could not be saved. Fix that first, then save the version.',
        );
        return;
      }
      const version = await api<DesignVersion>(`/api/projects/${projectId}/versions`, {
        method: 'POST',
        body: { name: trimmed, notes: notes.trim() },
      });
      onVersionSaved(version);
      await onReload();
      toast.success(`Saved v${version.number} “${version.name}”`);
      setDialog({ kind: 'none' });
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setFormError('A version with this name already exists in this project.');
      } else if (!isAuthError(error)) {
        setFormError(errorMessage(error));
      }
    } finally {
      setBusy(false);
    }
  };

  const submitRename = async (event: FormEvent) => {
    event.preventDefault();
    if (dialog.kind !== 'rename') return;
    const trimmed = name.trim();
    if (!trimmed) {
      setFormError('Give the version a name.');
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      await api<DesignVersion>(`/api/versions/${dialog.version.id}`, {
        method: 'PATCH',
        body: { name: trimmed, notes: notes.trim() },
      });
      await onReload();
      toast.success(`Renamed v${dialog.version.number}`);
      setDialog({ kind: 'none' });
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setFormError('A version with this name already exists in this project.');
      } else if (!isAuthError(error)) {
        setFormError(errorMessage(error));
      }
    } finally {
      setBusy(false);
    }
  };

  const restore = async (version: VersionSummary) => {
    setBusy(true);
    try {
      // Let any pending autosave land first so it cannot overwrite the restored draft later.
      await draft.flush();
      const restored = await api<Draft>(`/api/versions/${version.id}/restore`, { method: 'POST' });
      onRestored(restored, version);
      toast.success(`Restored v${version.number} “${version.name}” into the draft`);
      setDialog({ kind: 'none' });
    } catch (error) {
      fail(error, 'Could not restore the version');
    } finally {
      setBusy(false);
    }
  };

  const requestRestore = (version: VersionSummary) => {
    if (dirty) {
      setDialog({ kind: 'restore', version });
    } else {
      void restore(version);
    }
  };

  const duplicate = async (version: VersionSummary) => {
    setBusy(true);
    try {
      const copy = await api<DesignVersion>(`/api/versions/${version.id}/duplicate`, { method: 'POST', body: {} });
      await onReload();
      toast.success(`Duplicated v${version.number} as v${copy.number} “${copy.name}”`);
    } catch (error) {
      fail(error, 'Could not duplicate the version');
    } finally {
      setBusy(false);
    }
  };

  const remove = async (version: VersionSummary) => {
    setBusy(true);
    try {
      await api<void>(`/api/versions/${version.id}`, { method: 'DELETE' });
      onVersionDeleted(version.id);
      await onReload();
      toast.success(`Deleted v${version.number}`);
      setDialog({ kind: 'none' });
    } catch (error) {
      fail(error, 'Could not delete the version');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card" aria-labelledby="versions-heading">
      <div className="card-header">
        <h2 id="versions-heading">Versions</h2>
        <button type="button" className="button button-primary button-sm" data-testid="versions-save" onClick={openSave}>
          Save version
        </button>
      </div>
      <p className="row small muted" style={{ gap: '4px' }}>
        <span data-testid="draft-basis">{basisLabel}</span>
        <Explain
          label="draft basis"
          text={
            'The draft is the working copy you edit. "Based on" names the version it was last saved as or restored from. "(modified)" means the draft now differs from that version, so save a new version if you want to keep the changes.'
          }
        />
      </p>

      {loadError ? (
        <p className="form-error">
          Could not load versions: {loadError}{' '}
          <button type="button" className="button button-sm" onClick={() => void onReload()}>
            Retry
          </button>
        </p>
      ) : versions === null ? (
        <p className="loading small">Loading versions…</p>
      ) : versions.length === 0 ? (
        <EmptyState
          title="No versions yet"
          description="Save the draft as a version to keep a named snapshot you can restore or compare later."
        />
      ) : (
        <ul className="list" aria-label="Saved versions">
          {versions.map((version) => (
            <li
              key={version.id}
              className="list-item version-item"
              data-testid="version-item"
              data-version-number={version.number}
              data-version-id={version.id}
            >
              <div className="version-item-head">
                <span className="version-number" title="Version number: a permanent label within this project">
                  v{version.number}
                </span>
                <span className="version-name">{version.name}</span>
                {version.id === draft.basedOnVersionId ? <StatusPill tone="info">Basis</StatusPill> : null}
                <Menu
                  label={`Actions for v${version.number} ${version.name}`}
                  testId="version-menu"
                  items={[
                    { label: 'Restore into draft', testId: 'version-restore', onSelect: () => requestRestore(version) },
                    { label: 'Duplicate', testId: 'version-duplicate', onSelect: () => void duplicate(version) },
                    { label: 'Rename', testId: 'version-rename', onSelect: () => openRename(version) },
                    {
                      label: 'Delete',
                      testId: 'version-delete',
                      danger: true,
                      onSelect: () => setDialog({ kind: 'delete', version }),
                    },
                  ]}
                />
              </div>
              <div className="list-item-meta">{formatDateTime(version.created_at)}</div>
              {version.notes ? <div className="version-notes">{version.notes}</div> : null}
            </li>
          ))}
        </ul>
      )}

      <Modal
        open={dialog.kind === 'save' || dialog.kind === 'rename'}
        title={dialog.kind === 'rename' ? `Rename v${dialog.version.number}` : 'Save version'}
        onClose={closeDialog}
        footer={
          <>
            <button type="button" className="button" onClick={closeDialog} disabled={busy}>
              Cancel
            </button>
            <button
              type="submit"
              form="version-form"
              className="button button-primary"
              data-testid="version-save-confirm"
              disabled={busy}
            >
              {busy ? 'Saving…' : dialog.kind === 'rename' ? 'Rename' : 'Save version'}
            </button>
          </>
        }
      >
        <form
          id="version-form"
          className="stack-sm"
          onSubmit={(event) => void (dialog.kind === 'rename' ? submitRename(event) : submitSave(event))}
        >
          {dialog.kind === 'save' ? (
            <p className="small muted">The current draft (parameters and mission) is saved as a named snapshot.</p>
          ) : null}
          <div className="form-row">
            <label htmlFor="version-name">Name</label>
            <input
              id="version-name"
              className="input"
              data-testid="version-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={120}
              autoFocus
              required
            />
          </div>
          <div className="form-row">
            <label htmlFor="version-notes">Notes</label>
            <textarea
              id="version-notes"
              className="input"
              data-testid="version-notes"
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              rows={3}
              placeholder="What changed and why (optional)"
            />
          </div>
          {formError ? (
            <p className="form-error" role="alert">
              {formError}
            </p>
          ) : null}
        </form>
      </Modal>

      <ConfirmDialog
        open={dialog.kind === 'restore'}
        title={dialog.kind === 'restore' ? `Restore v${dialog.version.number}?` : 'Restore'}
        message="The draft has changes that are not saved in any version. Restoring replaces the draft with the selected version and those changes are lost. Save a version first if you want to keep them."
        confirmLabel="Restore and discard changes"
        danger
        busy={busy}
        onConfirm={() => {
          if (dialog.kind === 'restore') void restore(dialog.version);
        }}
        onCancel={closeDialog}
      />

      <ConfirmDialog
        open={dialog.kind === 'delete'}
        title={dialog.kind === 'delete' ? `Delete v${dialog.version.number}?` : 'Delete'}
        message={
          dialog.kind === 'delete'
            ? `Version v${dialog.version.number} “${dialog.version.name}” will be deleted permanently. The draft is not affected.`
            : ''
        }
        confirmLabel="Delete"
        danger
        busy={busy}
        onConfirm={() => {
          if (dialog.kind === 'delete') void remove(dialog.version);
        }}
        onCancel={closeDialog}
      />
    </section>
  );
}
