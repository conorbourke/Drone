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
import { WorkspaceContext, type TryVersionRequest, type WorkspaceApi } from '../lib/workspace';
import { createVersionFromPatch } from '../api/versions';
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

  // A new tab starts at the top of the page.
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [tab]);

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

  // Number the server will give the next saved version (monotonic, so a delete never lowers it).
  const [nextVersionNumber, setNextVersionNumber] = useState<number | null>(project.next_version_number ?? null);
  const onVersionCreated = useCallback((version: DesignVersion) => {
    setNextVersionNumber((current) => Math.max(current ?? 0, version.number + 1));
  }, []);

  // Settings: limits for the MTOW banner, thresholds for the engine checks.
  const [settings, setSettings] = useState<Settings | null>(null);
  const limits = settings?.limits ?? null;
  useEffect(() => {
    const controller = new AbortController();
    api<SettingsResponse>('/api/settings', { signal: controller.signal })
      .then((response) => setSettings(response.settings))
      .catch((caught: unknown) => {
        if (isAbortError(caught) || isAuthError(caught)) return;
        toast.error(`Could not load the settings (mass limits and check thresholds): ${errorMessage(caught)}`);
      });
    return () => controller.abort();
  }, [toast]);

  // Full documents of versions, used to tell whether the draft differs from its basis.
  const [versionDocs, setVersionDocs] = useState<Record<number, DesignVersion>>({});
  // Id of a basis whose document could not be fetched (deleted elsewhere, or a network error).
  const [basisUnavailable, setBasisUnavailable] = useState<number | null>(null);
  const basedOn = draft.basedOnVersionId;
  const basisDoc = basedOn !== null ? (versionDocs[basedOn] ?? null) : null;
  const basisLoading = basedOn !== null && !basisDoc && basisUnavailable !== basedOn;

  useEffect(() => {
    if (basedOn === null || basisDoc) return;
    const controller = new AbortController();
    api<DesignVersion>(`/api/versions/${basedOn}`, { signal: controller.signal })
      .then((version) => setVersionDocs((docs) => ({ ...docs, [version.id]: version })))
      .catch((caught: unknown) => {
        if (isAbortError(caught) || isAuthError(caught)) return;
        // A 404 means the basis was deleted elsewhere; the label simply shows no basis.
        setBasisUnavailable(basedOn);
      });
    return () => controller.abort();
  }, [basedOn, basisDoc]);

  // null while the basis document is still on its way: no verdict can be given yet.
  const dirty = useMemo(
    () => (basisLoading ? null : isDraftDirty(draft.doc, basisDoc)),
    [basisLoading, draft.doc, basisDoc],
  );
  const basisNumber = basedOn === null ? null : (versions?.find((v) => v.id === basedOn)?.number ?? basisDoc?.number ?? null);
  const basisLabel = draftBasisLabel(basisNumber, dirty);
  // Anything changed locally since the document was loaded: the only evidence available while
  // the basis document is unknown.
  const edited = draft.edited || draft.status !== 'saved';
  // Restoring discards edits when the draft differs from its basis, when that cannot be told yet
  // (basis still loading, or unavailable after an edit), or when no version holds the draft at all.
  const restoreNeedsConfirm =
    basedOn !== null && !basisDoc
      ? basisLoading || edited
      : dirty === true || (basedOn === null && (versions?.length ?? 0) > 0);

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

  // Shared with the analysis, scale and assistant panels.
  const versionCreated = useCallback(
    async (version: DesignVersion, compareWith: number | null) => {
      onVersionCreated(version);
      await reloadVersions();
      const link =
        compareWith !== null && compareWith !== version.number
          ? { label: `Compare with v${compareWith}`, to: `/projects/${project.id}/compare?versions=${Math.min(compareWith, version.number)},${Math.max(compareWith, version.number)}` }
          : undefined;
      toast.success(`Saved as v${version.number} “${version.name}”.`, link);
    },
    [onVersionCreated, reloadVersions, project.id, toast],
  );
  const flushDraft = draft.flush;
  const tryAsNewVersion = useCallback(
    async (request: TryVersionRequest) => {
      try {
        if (request.base === 'draft') await flushDraft();
        const version = await createVersionFromPatch(
          project.id,
          { name: request.name, notes: request.notes, base: request.base, patch: request.patch },
          (versions ?? []).map((v) => v.name),
        );
        await versionCreated(version, request.compareWith);
        return version;
      } catch (caught) {
        if (!isAuthError(caught)) toast.error(`Could not save the new version: ${errorMessage(caught)}`);
        return null;
      }
    },
    [flushDraft, project.id, versions, versionCreated, toast],
  );
  const workspace = useMemo<WorkspaceApi>(
    () => ({
      projectId: project.id,
      versions,
      basisNumber,
      draftStatus: draft.status,
      draftSavedAt: draft.savedAt,
      flushDraft,
      tryAsNewVersion,
      versionCreated,
    }),
    [project.id, versions, basisNumber, draft.status, draft.savedAt, flushDraft, tryAsNewVersion, versionCreated],
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
      content = (
        <DesignTab doc={draft.doc} update={draft.update} fieldErrors={draft.fieldErrors} settings={settings} versions={versions} />
      );
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
      content = (
        <InputsTab
          doc={draft.doc}
          update={draft.update}
          fieldErrors={draft.fieldErrors}
          projectId={project.id}
          settings={settings}
        />
      );
  }

  return (
    <WorkspaceContext.Provider value={workspace}>
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
            nextVersionNumber={nextVersionNumber}
            onVersionCreated={onVersionCreated}
            onVersionSaved={onVersionSaved}
            onRestored={onRestored}
            onVersionDeleted={onVersionDeleted}
          />
          <AssistantPanel projectId={project.id} doc={draft.doc} />
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
    </WorkspaceContext.Provider>
  );
}
