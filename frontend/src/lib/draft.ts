/**
 * Pure helpers for the project draft: structural JSON comparison, dirty tracking against
 * the version the draft is based on, and immutable dotted-path updates.
 *
 * The React hook that owns the debounced autosave lives in lib/useDraft.ts; everything here
 * is side-effect free and unit-tested.
 */
import type { DraftDocument } from '../api/types';

/** Milliseconds of idle time before an edited draft is sent to the server. */
export const AUTOSAVE_DELAY_MS = 800;

export type SaveStatus = 'saved' | 'saving' | 'unsaved' | 'error';

/** The text shown in the draft-status indicator. */
export function saveStatusLabel(status: SaveStatus, errorMessage?: string | null): string {
  switch (status) {
    case 'saved':
      return 'Saved';
    case 'saving':
      return 'Saving…';
    case 'unsaved':
      return 'Unsaved changes';
    case 'error':
      return errorMessage ? `Could not save: ${errorMessage}` : 'Could not save';
  }
}

type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Structural equality with JSON semantics: key order is ignored, `undefined` properties are
 * treated as absent, NaN equals NaN, and -0 equals 0 (JSON cannot tell them apart).
 */
export function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a === 'number' && typeof b === 'number') {
    return Number.isNaN(a) && Number.isNaN(b);
  }
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    for (let i = 0; i < a.length; i += 1) {
      if (!deepEqual(a[i], b[i])) return false;
    }
    return true;
  }
  if (isPlainObject(a) && isPlainObject(b)) {
    const keysA = Object.keys(a).filter((k) => a[k] !== undefined);
    const keysB = Object.keys(b).filter((k) => b[k] !== undefined);
    if (keysA.length !== keysB.length) return false;
    for (const key of keysA) {
      if (!Object.prototype.hasOwnProperty.call(b, key)) return false;
      if (!deepEqual(a[key], b[key])) return false;
    }
    return true;
  }
  return false;
}

/** Only the parts of a draft that a version snapshots. */
export function draftDocument(doc: DraftDocument): DraftDocument {
  return { parameters: doc.parameters, mission: doc.mission };
}

/**
 * True when the draft differs from the version it is based on. With no basis the draft
 * cannot be compared, so it is reported as not dirty.
 */
export function isDraftDirty(draft: DraftDocument, basis: DraftDocument | null | undefined): boolean {
  if (!basis) return false;
  return !deepEqual(draftDocument(draft), draftDocument(basis));
}

/** Text for the draft-basis indicator: "Draft based on v3 (modified)". */
export function draftBasisLabel(basisNumber: number | null | undefined, dirty: boolean): string {
  if (basisNumber === null || basisNumber === undefined) return 'Draft not based on a saved version';
  return dirty ? `Draft based on v${basisNumber} (modified)` : `Draft based on v${basisNumber}`;
}

/** Read a dotted path such as "wing.span_mm"; undefined when any segment is missing. */
export function getAtPath(doc: unknown, path: string): unknown {
  let current: unknown = doc;
  for (const segment of path.split('.')) {
    if (!isPlainObject(current)) return undefined;
    current = current[segment];
  }
  return current;
}

/**
 * Return a copy of `doc` with the value at the dotted path replaced. Only the objects along
 * the path are copied; untouched branches keep their identity. Missing intermediate objects
 * are created.
 */
export function setAtPath<T>(doc: T, path: string, value: unknown): T {
  const segments = path.split('.');
  const update = (node: unknown, index: number): unknown => {
    if (index === segments.length) return value;
    const key = segments[index]!;
    const base = isPlainObject(node) ? node : {};
    return { ...base, [key]: update(base[key], index + 1) };
  };
  return update(doc, 0) as T;
}

/** Deep copy through JSON so later edits cannot leak into cached snapshots. */
export function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export type { JsonValue };
