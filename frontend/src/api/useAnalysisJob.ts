/**
 * Polls one analysis job (`GET /api/analyses/{id}`) every 1.5 s while it is queued or running
 * and stops once it is done or failed. The result arrives while the job is still running (the
 * recommendation sweep fills `result.recommendations` afterwards), so callers render whatever
 * the latest poll returned.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { AnalysisDetail } from './analysis';
import { api, errorMessage, isAbortError, isAuthError } from './client';

export const POLL_MS = 1500;

export function isActive(status: string | undefined): boolean {
  return status === 'queued' || status === 'running';
}

export function useAnalysisJob<R>(id: number | null) {
  const [job, setJob] = useState<AnalysisDetail<R> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (id === null) return;
    let cancelled = false;
    const controller = new AbortController();
    const tick = async () => {
      try {
        const next = await api<AnalysisDetail<R>>(`/api/analyses/${id}`, { signal: controller.signal });
        if (cancelled) return;
        setJob(next);
        setError(null);
        if (isActive(next.status)) timer.current = window.setTimeout(() => void tick(), POLL_MS);
      } catch (caught) {
        if (cancelled || isAbortError(caught) || isAuthError(caught)) return;
        setError(errorMessage(caught));
        // A network blip should not end the polling; try again a little later.
        timer.current = window.setTimeout(() => void tick(), POLL_MS * 2);
      }
    };
    void tick();
    return () => {
      cancelled = true;
      controller.abort();
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, [id]);

  const reset = useCallback(() => {
    setJob(null);
    setError(null);
  }, []);

  // A job from a previous id is never shown for a new one.
  const current = job && job.id === id ? job : null;
  return { job: current, error, reset };
}
