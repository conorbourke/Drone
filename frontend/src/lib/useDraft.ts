/**
 * Owns the project draft on the client: immediate local updates, a debounced PUT /draft
 * (800 ms after the last edit), the Saved / Saving… / Unsaved changes / error status, and
 * the pointer to the version the draft is based on.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, api, errorMessage, fieldErrorPath } from '../api/client';
import type { Draft, DraftDocument } from '../api/types';
import { AUTOSAVE_DELAY_MS, setAtPath, type SaveStatus } from './draft';

const NO_FIELD_ERRORS: Record<string, string> = {};

/** Map a 422 response to messages keyed by dotted path ("mission.payload_max_g"). */
function fieldErrorsOf(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError) || error.fields.length === 0) return NO_FIELD_ERRORS;
  const result: Record<string, string> = {};
  for (const field of error.fields) {
    const path = fieldErrorPath(field);
    if (path && !result[path]) result[path] = field.msg;
  }
  return result;
}

export interface DraftController {
  /** Current local document (parameters + mission). */
  doc: DraftDocument;
  status: SaveStatus;
  /** Plain-language message when status is "error". */
  errorMessage: string | null;
  /** Server validation messages keyed by dotted path, empty unless the last save returned 422. */
  fieldErrors: Record<string, string>;
  /** Version id the draft was last saved as or restored from; null when none. */
  basedOnVersionId: number | null;
  /** ISO timestamp of the last successful save. */
  savedAt: string | null;
  /** Update one dotted path (e.g. "parameters.wing.span_mm"); schedules an autosave. */
  update: (path: string, value: unknown) => void;
  /** Replace the whole document with server state (after a restore); marks it saved. */
  replace: (doc: DraftDocument, basedOnVersionId: number | null, savedAt?: string | null) => void;
  /** Point the draft at a version without changing the document (after saving a version). */
  setBasedOn: (versionId: number | null) => void;
  /** Save now if anything is pending. Resolves true when the server holds the latest edits. */
  flush: () => Promise<boolean>;
}

export function useDraft(projectId: number, initial: Draft): DraftController {
  const [doc, setDoc] = useState<DraftDocument>(() => ({
    parameters: initial.parameters,
    mission: initial.mission,
  }));
  const [status, setStatus] = useState<SaveStatus>('saved');
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>(NO_FIELD_ERRORS);
  const [basedOnVersionId, setBasedOnVersionId] = useState<number | null>(initial.based_on_version_id);
  const [savedAt, setSavedAt] = useState<string | null>(initial.updated_at);

  // Mutable bookkeeping that must not trigger renders.
  const latestDoc = useRef<DraftDocument>(doc);
  const editSeq = useRef(0); // bumped on every local edit
  const savedSeq = useRef(0); // editSeq value last persisted
  const generation = useRef(0); // bumped by replace(); stale responses are ignored
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inflight = useRef<Promise<boolean> | null>(null);

  const clearTimer = useCallback(() => {
    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const performSave = useCallback(
    async (keepalive = false): Promise<boolean> => {
      clearTimer();
      if (inflight.current) {
        await inflight.current;
      }
      if (savedSeq.current === editSeq.current) return true;

      const seq = editSeq.current;
      const gen = generation.current;
      const snapshot = latestDoc.current;
      setStatus('saving');
      const run = (async () => {
        try {
          const saved = await api<Draft>(`/api/projects/${projectId}/draft`, {
            method: 'PUT',
            body: { parameters: snapshot.parameters, mission: snapshot.mission },
            keepalive,
          });
          if (gen !== generation.current) return true; // document was replaced meanwhile
          savedSeq.current = Math.max(savedSeq.current, seq);
          setBasedOnVersionId(saved.based_on_version_id);
          setSavedAt(saved.updated_at);
          if (editSeq.current === seq) {
            setStatus('saved');
            setError(null);
            setFieldErrors(NO_FIELD_ERRORS);
          }
          return true;
        } catch (e) {
          if (gen !== generation.current) return false;
          if (editSeq.current === seq) {
            setStatus('error');
            setError(errorMessage(e));
            setFieldErrors(fieldErrorsOf(e));
          }
          return false;
        } finally {
          inflight.current = null;
        }
      })();
      inflight.current = run;
      return run;
    },
    [projectId, clearTimer],
  );

  const schedule = useCallback(() => {
    clearTimer();
    timer.current = setTimeout(() => {
      timer.current = null;
      void performSave();
    }, AUTOSAVE_DELAY_MS);
  }, [clearTimer, performSave]);

  const update = useCallback(
    (path: string, value: unknown) => {
      const next = setAtPath(latestDoc.current, path, value);
      latestDoc.current = next;
      editSeq.current += 1;
      setDoc(next);
      setStatus('unsaved');
      setError(null);
      schedule();
    },
    [schedule],
  );

  const replace = useCallback(
    (nextDoc: DraftDocument, nextBasedOn: number | null, nextSavedAt: string | null = null) => {
      clearTimer();
      generation.current += 1;
      latestDoc.current = nextDoc;
      savedSeq.current = editSeq.current;
      setDoc(nextDoc);
      setStatus('saved');
      setError(null);
      setFieldErrors(NO_FIELD_ERRORS);
      setBasedOnVersionId(nextBasedOn);
      if (nextSavedAt) setSavedAt(nextSavedAt);
    },
    [clearTimer],
  );

  const setBasedOn = useCallback((versionId: number | null) => {
    setBasedOnVersionId(versionId);
  }, []);

  const flush = useCallback(() => performSave(), [performSave]);

  // Save pending edits when the page is hidden or the workspace unmounts, using keepalive so
  // the request survives navigation. No beforeunload prompt: the autosave is the safety net.
  useEffect(() => {
    const onPageHide = () => {
      if (savedSeq.current !== editSeq.current) void performSave(true);
    };
    window.addEventListener('pagehide', onPageHide);
    return () => {
      window.removeEventListener('pagehide', onPageHide);
      if (savedSeq.current !== editSeq.current) void performSave(true);
      clearTimer();
    };
  }, [performSave, clearTimer]);

  return useMemo(
    () => ({
      doc,
      status,
      errorMessage: error,
      fieldErrors,
      basedOnVersionId,
      savedAt,
      update,
      replace,
      setBasedOn,
      flush,
    }),
    [doc, status, error, fieldErrors, basedOnVersionId, savedAt, update, replace, setBasedOn, flush],
  );
}
