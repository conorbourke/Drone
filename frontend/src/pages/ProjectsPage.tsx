/** Projects list with create, rename and delete. */
import { useCallback, useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useNavigate } from 'react-router';
import { ApiError, api, errorMessage, isAbortError, isAuthError } from '../api/client';
import type { Project, ProjectSummary } from '../api/types';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { EmptyState } from '../components/EmptyState';
import { Modal } from '../components/Modal';
import { useToast } from '../components/Toast';
import { AppShell } from '../layout/AppShell';
import { formatDateTime, pluralize } from '../lib/format';

type Dialog = { kind: 'none' } | { kind: 'create' } | { kind: 'rename'; project: ProjectSummary } | { kind: 'delete'; project: ProjectSummary };

export function ProjectsPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialog>({ kind: 'none' });
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    (signal?: AbortSignal) =>
      api<ProjectSummary[]>('/api/projects', { signal })
        .then((list) => {
          setProjects(list);
          setLoadError(null);
        })
        .catch((error: unknown) => {
          if (isAbortError(error) || isAuthError(error)) return;
          setLoadError(errorMessage(error));
        }),
    [],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const closeDialog = () => {
    if (busy) return;
    setDialog({ kind: 'none' });
    setFormError(null);
  };

  const openCreate = () => {
    setName('');
    setDescription('');
    setFormError(null);
    setDialog({ kind: 'create' });
  };

  const openRename = (project: ProjectSummary) => {
    setName(project.name);
    setDescription(project.description ?? '');
    setFormError(null);
    setDialog({ kind: 'rename', project });
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setFormError('Give the project a name.');
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      if (dialog.kind === 'create') {
        const project = await api<Project>('/api/projects', {
          method: 'POST',
          body: { name: trimmed, description: description.trim() },
        });
        toast.success(`Created “${project.name}”`);
        setDialog({ kind: 'none' });
        navigate(`/projects/${project.id}?tab=inputs`);
        return;
      }
      if (dialog.kind === 'rename') {
        await api<Project>(`/api/projects/${dialog.project.id}`, {
          method: 'PATCH',
          body: { name: trimmed, description: description.trim() },
        });
        toast.success('Project updated');
        setDialog({ kind: 'none' });
        await load();
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setFormError('A project with this name already exists.');
      } else if (!isAuthError(error)) {
        setFormError(errorMessage(error));
      }
    } finally {
      setBusy(false);
    }
  };

  const remove = async (project: ProjectSummary) => {
    setBusy(true);
    try {
      await api<void>(`/api/projects/${project.id}`, { method: 'DELETE' });
      toast.success(`Deleted “${project.name}”`);
      setDialog({ kind: 'none' });
      await load();
    } catch (error) {
      if (!isAuthError(error)) toast.error(`Could not delete the project: ${errorMessage(error)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AppShell>
      <div className="page-header">
        <h1>Projects</h1>
        <span className="spacer" />
        <button type="button" className="button button-primary" data-testid="projects-new" onClick={openCreate}>
          New project
        </button>
      </div>

      {loadError ? (
        <EmptyState
          title="Could not load projects"
          description={loadError}
          action={
            <button type="button" className="button" onClick={() => void load()}>
              Try again
            </button>
          }
        />
      ) : projects === null ? (
        <div className="project-grid" aria-busy="true">
          <div className="skeleton" />
          <div className="skeleton" />
        </div>
      ) : projects.length === 0 ? (
        <EmptyState
          title="No projects yet"
          description="A project holds one aircraft design: its mission, the editable draft and the saved versions. Create your first one to begin."
          action={
            <button type="button" className="button button-primary" onClick={openCreate}>
              Create a project
            </button>
          }
        />
      ) : (
        <ul className="project-grid list" aria-label="Projects">
          {projects.map((project) => (
            <li key={project.id} className="card project-card" data-testid="project-card" data-project-id={project.id}>
              <h2 className="project-card-title">
                <Link to={`/projects/${project.id}?tab=inputs`} data-testid="project-open">
                  {project.name}
                </Link>
              </h2>
              {project.description ? <p className="project-card-description">{project.description}</p> : null}
              <p className="list-item-meta">
                {pluralize(project.version_count, 'version')}
                {project.latest_version
                  ? ` · latest v${project.latest_version.number} “${project.latest_version.name}”`
                  : ''}
                <br />
                Updated {formatDateTime(project.updated_at)}
              </p>
              <div className="project-card-actions">
                <Link to={`/projects/${project.id}?tab=inputs`} className="button button-sm button-primary">
                  Open
                </Link>
                <button type="button" className="button button-sm" data-testid="project-rename" onClick={() => openRename(project)}>
                  Rename
                </button>
                <button
                  type="button"
                  className="button button-sm button-ghost"
                  data-testid="project-delete"
                  onClick={() => setDialog({ kind: 'delete', project })}
                >
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <Modal
        open={dialog.kind === 'create' || dialog.kind === 'rename'}
        title={dialog.kind === 'rename' ? 'Rename project' : 'New project'}
        onClose={closeDialog}
        footer={
          <>
            <button type="button" className="button" onClick={closeDialog} disabled={busy}>
              Cancel
            </button>
            <button
              type="submit"
              form="project-form"
              className="button button-primary"
              data-testid={dialog.kind === 'rename' ? 'project-rename-confirm' : 'project-create'}
              disabled={busy}
            >
              {busy ? 'Saving…' : dialog.kind === 'rename' ? 'Save' : 'Create'}
            </button>
          </>
        }
      >
        <form id="project-form" className="stack-sm" onSubmit={(event) => void submit(event)}>
          <div className="form-row">
            <label htmlFor="project-name">Name</label>
            <input
              id="project-name"
              className="input"
              data-testid="project-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={120}
              autoFocus
              required
            />
          </div>
          <div className="form-row">
            <label htmlFor="project-description">Description</label>
            <textarea
              id="project-description"
              className="input"
              data-testid="project-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={3}
              placeholder="What this design is for (optional)"
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
        open={dialog.kind === 'delete'}
        title={dialog.kind === 'delete' ? `Delete “${dialog.project.name}”?` : 'Delete project'}
        message="The project, its draft and every saved version will be deleted permanently. This cannot be undone."
        confirmLabel="Delete project"
        danger
        busy={busy}
        onConfirm={() => {
          if (dialog.kind === 'delete') void remove(dialog.project);
        }}
        onCancel={closeDialog}
      />
    </AppShell>
  );
}
