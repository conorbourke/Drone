/**
 * What the project workspace shares with the analysis, scale and assistant panels: the versions
 * list and one "Try as new version" action that saves a patch as a version, refreshes the
 * Versions panel and shows a toast with a link to compare it.
 */
import { createContext, useContext } from 'react';
import type { DesignVersion, VersionSummary } from '../api/types';
import type { PatchBase } from '../api/versions';

export interface TryVersionRequest {
  name: string;
  notes?: string;
  base: PatchBase;
  patch: Record<string, unknown>;
  /** Version number to compare the new version with in the toast link; null for none. */
  compareWith: number | null;
}

export interface WorkspaceApi {
  projectId: number;
  versions: VersionSummary[] | null;
  /** Number of the version the draft is based on, if any. */
  basisNumber: number | null;
  /** Draft save state and the server time of its last save (for analysis staleness). */
  draftStatus: 'saved' | 'saving' | 'unsaved' | 'error';
  draftSavedAt: string | null;
  /** Saves pending draft edits; resolves true when the server holds them. */
  flushDraft: () => Promise<boolean>;
  /** Save a patch as a new version (Try as new version). Resolves with it, or null on failure (already reported). */
  tryAsNewVersion: (request: TryVersionRequest) => Promise<DesignVersion | null>;
  /** Tell the workspace about a version created elsewhere (scale dialog). */
  versionCreated: (version: DesignVersion, compareWith: number | null) => Promise<void>;
}

export const WorkspaceContext = createContext<WorkspaceApi | null>(null);

export function useWorkspace(): WorkspaceApi {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error('useWorkspace outside the project workspace');
  return ctx;
}
