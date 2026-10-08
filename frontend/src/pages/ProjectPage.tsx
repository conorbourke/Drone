/**
 * Project workspace: loads the project, owns the draft (autosave), the versions list, the
 * basis version for dirty tracking and the MTOW banner, and renders the active tab with the
 * Versions and Assistant panels in the right rail.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router';
import { ApiError, api, errorMessage, isAbortError, isAuthError } from '../api/client';
import type { DesignVersion, Draft, Project, Settings, SettingsResponse, VersionSummary } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { Explain } from '../components/Explain';
import { StatusPill } from '../components/StatusPill';
import { useToast } from '../components/Toast';
import { AppShell, WorkspaceTabs, parseTab } from '../layout/AppShell';
import { draftBasisLabel, isDraftDirty, saveStatusLabel } from '../lib/draft';
import { formatMassKg } from '../lib/format';
import { useDraft } from '../lib/useDraft';
import { AssistantPanel } from '../panels/AssistantPanel';
import { VersionsPanel } from '../panels/VersionsPanel';
import { DesignTab } from '../tabs/DesignTab';
import { FilesTab } from '../tabs/FilesTab';
import { FlightDataTab } from '../tabs/FlightDataTab';
import { InputsTab } from '../tabs/InputsTab';
import { PartsTab } from '../tabs/PartsTab';

export function ProjectPage() {
  const params = useParams<{ id: string }>();
  const projectId = Number(params.id);
  const validId = Number.isInteger(projectId) && projectId > 0;
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<{ status: number; message: string } | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!validId) return;
    const controller = new AbortController();
    api<Project>(`/api/projects/${projectId}`, { signal: controller.signal })
      .then((loaded) => {
        setProject(loaded);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (isAbortError(caught) || isAuthError(caught)) return;
        setError({ status: caught instanceof ApiError ? caught.status : 0, message: errorMessage(caught) });
      });
    return () => controller.abort();
  }, [projectId, validId, attempt]);

  if (!validId || error?.status === 404) {
    return (
      <AppShell narrow>
        <EmptyState
          title="Project not found"
          description="It may have been deleted, or the link is wrong."
          action={
            <Link to="/" className="button button-primary">
              Back to projects
            </Link>
          }
        />
      </AppShell>
    );
  }

  if (error) {
    return (
      <AppShell narrow>
        <EmptyState
          title="Could not load the project"
          description={error.message}
          action={
            <button type="button" className="button" onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </button>
          }
        />
      </AppShell>
    );
  }

  if (!project || project.id !== projectId) {
    return (
      <AppShell title="Loading…" tabs={<WorkspaceTabs active="inputs" />} rail={<div className="skeleton" />}>
        <div className="skeleton" aria-busy="true" aria-label="Loading project" />
      </AppShell>
    );
  }

  return <ProjectWorkspace key={project.id} project={project} />;
}

function ProjectWorkspace({ project }: { project: Project }) {
  const toast = useToast();
  const [searchParams] = useSearchParams();
  const tab = parseTab(searchParams.get('tab'));
  const draft = useDraft(project.id, project.draft);

  // Versions list.
  const [versions, setVersions] = useState<VersionSummary[] | null>(null);
  const [versionsError, setVersionsError] = useState<string | null>(null);
  const reloadVersions = useCallback(
    () =>
      api<VersionSummary[]>(`/api/projects/${project.id}/versions`)
        .then((list) => {
          setVersions(list);
          setVersionsError(null);
        })
        .catch((caught: unknown) => {
          if (isAuthError(caught)) return;
          setVersionsError(errorMessage(caught));
        }),
    [project.id],
  );

  useEffect(() => {
    void reloadVersions();
  }, [reloadVersions]);

  // Limits for the MTOW banner.
  const [limits, setLimits] = useState<Settings['limits'] | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    api<SettingsResponse>('/api/settings', { signal: controller.signal })
      .then((response) => setLimits(response.settings.limits))
      .catch((caught: unknown) => {
        if (isAbortError(caught) || isAuthError(caught)) return;
        toast.error(`Could not load the mass limits: ${errorMessage(caught)}`);
      });
    return () => controller.abort();
  }, [toast]);

  // Full documents of versions, used to tell whether the draft differs from its basis.
  const [versionDocs, setVersionDocs] = useState<Record<number, DesignVersion>>({});
  const basedOn = draft.basedOnVersionId;
  const basisDoc = basedOn !== null ? (versionDocs[basedOn] ?? null) : null;

  useEffect(() => {
    if (basedOn === null || basisDoc) return;
    const controller = new AbortController();
    api<DesignVersion>(`/api/versions/${basedOn}`, { signal: controller.signal })
      .then((version) => setVersionDocs((docs) => ({ ...docs, [version.id]: version })))
      .catch((caught: unknown) => {
        if (isAbortError(caught) || isAuthError(caught)) return;
        // A 404 means the basis was deleted elsewhere; the label simply shows no basis.
      });
    return () => controller.abort();
  }, [basedOn, basisDoc]);

  const dirty = useMemo(() => isDraftDirty(draft.doc, basisDoc), [draft.doc, basisDoc]);
  const basisNumber = basedOn === null ? null : (versions?.find((v) => v.id === basedOn)?.number ?? basisDoc?.number ?? null);
  const basisLabel = draftBasisLabel(basisNumber, dirty);
  // Restoring discards edits when the draft differs from its basis, or when no version holds it at all.
  const restoreNeedsConfirm = dirty || (basedOn === null && (versions?.length ?? 0) > 0);

  const onVersionSaved = useCallback(
    (version: DesignVersion) => {
      setVersionDocs((docs) => ({ ...docs, [version.id]: version }));
      draft.setBasedOn(version.id);
    },
    [draft],
  );

  const onRestored = useCallback(
    (restored: Draft, version: VersionSummary) => {
      setVersionDocs((docs) =>
        docs[version.id]
          ? docs
          : {
              ...docs,
              [version.id]: {
                ...version,
                project_id: project.id,
                parameters: restored.parameters,
                mission: restored.mission,
              },
            },
      );
      draft.replace(
        { parameters: restored.parameters, mission: restored.mission },
        restored.based_on_version_id,
        restored.updated_at,
      );
    },
    [draft, project.id],
  );

  const onVersionDeleted = useCallback(
    (versionId: number) => {
      if (draft.basedOnVersionId === versionId) draft.setBasedOn(null);
      setVersionDocs((docs) => {
        if (!docs[versionId]) return docs;
        const next = { ...docs };
        delete next[versionId];
        return next;
      });
    },
    [draft],
  );

  // MTOW banner.
  const mass = draft.doc.mission.target_takeoff_mass_kg;
  let banner: { level: 'warn' | 'error'; title: string; text: string } | null = null;
  if (limits && Number.isFinite(mass) && mass >= limits.warn_mtow_kg) {
    if (mass > limits.design_mtow_kg) {
      banner = {
        level: 'error',
        title: `Target take-off mass ${formatMassKg(mass)} exceeds the design limit of ${formatMassKg(limits.design_mtow_kg)}.`,
        text: `The legal limit for EU Open A3 is ${formatMassKg(limits.legal_mtow_kg)}; the design limit keeps a safety margin below it. Reduce the target mass or change the limits in Settings.`,
      };
    } else {
      banner = {
        level: 'warn',
        title: `Target take-off mass ${formatMassKg(mass)} is close to the design limit of ${formatMassKg(limits.design_mtow_kg)}.`,
        text: `Warnings start at ${formatMassKg(limits.warn_mtow_kg)}. The legal limit (EU Open A3) is ${formatMassKg(limits.legal_mtow_kg)}. Both thresholds can be changed in Settings.`,
      };
    }
  }

  const statusTone = draft.status === 'saved' ? 'ok' : draft.status === 'error' ? 'error' : draft.status === 'saving' ? 'info' : 'warn';

  let content;
  switch (tab) {
    case 'design':
      content = <DesignTab doc={draft.doc} update={draft.update} fieldErrors={draft.fieldErrors} />;
      break;
    case 'parts':
      content = <PartsTab />;
      break;
    case 'files':
      content = <FilesTab />;
      break;
    case 'flight':
      content = <FlightDataTab />;
      break;
    default:
      content = <InputsTab doc={draft.doc} update={draft.update} fieldErrors={draft.fieldErrors} />;
  }

  return (
    <AppShell
      title={project.name}
      tabs={<WorkspaceTabs active={tab} />}
      rail={
        <>
          <VersionsPanel
            projectId={project.id}
            versions={versions}
            loadError={versionsError}
            onReload={reloadVersions}
            draft={draft}
            basisLabel={basisLabel}
            dirty={restoreNeedsConfirm}
            onVersionSaved={onVersionSaved}
            onRestored={onRestored}
            onVersionDeleted={onVersionDeleted}
          />
          <AssistantPanel />
        </>
      }
    >
      <div className="workspace-header">
        <h1 className="visually-hidden">{project.name}</h1>
        <StatusPill tone={statusTone} testId="draft-status" title={draft.errorMessage ?? undefined}>
          {saveStatusLabel(draft.status, draft.errorMessage)}
        </StatusPill>
        <Explain
          label="draft status"
          text="Edits are kept in the draft and saved automatically about a second after you stop typing. Save a version when you want to keep a named snapshot."
        />
        <span className="spacer" />
        <span className="small muted">{project.description}</span>
      </div>

      {banner ? (
        <div
          className={`banner banner-${banner.level}`}
          role={banner.level === 'error' ? 'alert' : 'status'}
          data-testid="mtow-banner"
          data-level={banner.level}
        >
          <div>
            <div className="banner-title">{banner.title}</div>
            <div className="small">{banner.text}</div>
          </div>
        </div>
      ) : null}

      {content}
    </AppShell>
  );
}
