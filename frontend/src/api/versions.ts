/** "Try as new version" and "Save as new version" calls shared by the analysis, scale and assistant panels. */
import { ApiError, api } from './client';
import type { DesignVersion } from './types';
import { uniqueName } from '../lib/analysis';

export type PatchBase = 'draft' | { version_id: number };

/**
 * POST /versions/from-patch with a default name; when the name is taken (409) the next free
 * "name (2)", "name (3)"… is tried, a few times at most.
 */
export async function createVersionFromPatch(
  projectId: number,
  body: { name: string; notes?: string; base: PatchBase; patch: Record<string, unknown> },
  takenNames: string[],
): Promise<DesignVersion> {
  const taken = [...takenNames];
  let name = uniqueName(body.name, taken);
  for (let attempt = 0; ; attempt++) {
    try {
      return await api<DesignVersion>(`/api/projects/${projectId}/versions/from-patch`, {
        method: 'POST',
        body: { name, notes: body.notes ?? '', base: body.base, patch: body.patch },
      });
    } catch (error) {
      if (error instanceof ApiError && error.status === 409 && attempt < 4) {
        taken.push(name);
        name = uniqueName(body.name, taken);
        continue;
      }
      throw error;
    }
  }
}

/** POST /versions with explicit parameters and mission (used for a scaled design). */
export async function createVersionFromDocuments(
  projectId: number,
  body: { name: string; notes?: string; parameters: unknown; mission: unknown },
  takenNames: string[],
): Promise<DesignVersion> {
  const taken = [...takenNames];
  let name = uniqueName(body.name, taken);
  for (let attempt = 0; ; attempt++) {
    try {
      return await api<DesignVersion>(`/api/projects/${projectId}/versions`, {
        method: 'POST',
        body: { name, notes: body.notes ?? '', parameters: body.parameters, mission: body.mission },
      });
    } catch (error) {
      if (error instanceof ApiError && error.status === 409 && attempt < 4) {
        taken.push(name);
        name = uniqueName(body.name, taken);
        continue;
      }
      throw error;
    }
  }
}
