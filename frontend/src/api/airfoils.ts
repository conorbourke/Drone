/**
 * The airfoil library (GET /api/airfoils) and unit-chord coordinates (GET /api/airfoils/{id}),
 * loaded once per page life and shared by the engine, the 3D view and the drawings.
 */
import { useEffect, useMemo, useState } from 'react';
import { api, isAbortError } from './client';
import type { AirfoilDetail, AirfoilSummary } from './types';
import type { AirfoilSummaryMap } from '../engine';

let summaries: AirfoilSummaryMap | null = null;
let summariesInflight: Promise<AirfoilSummaryMap> | null = null;
const coordinateCache = new Map<string, [number, number][]>();
const coordinateInflight = new Map<string, Promise<[number, number][] | null>>();

export function loadAirfoils(): Promise<AirfoilSummaryMap> {
  if (summaries) return Promise.resolve(summaries);
  if (!summariesInflight) {
    summariesInflight = api<AirfoilSummary[]>('/api/airfoils')
      .then((list) => {
        summaries = Object.fromEntries(list.map((a) => [a.id, a]));
        return summaries;
      })
      .finally(() => {
        summariesInflight = null;
      });
  }
  return summariesInflight;
}

function loadCoordinates(id: string): Promise<[number, number][] | null> {
  const cached = coordinateCache.get(id);
  if (cached) return Promise.resolve(cached);
  let pending = coordinateInflight.get(id);
  if (!pending) {
    pending = api<AirfoilDetail>(`/api/airfoils/${encodeURIComponent(id)}`)
      .then((detail) => {
        coordinateCache.set(id, detail.coordinates);
        return detail.coordinates;
      })
      // Unknown ids fall back to the engine's NACA-style section; nothing to report.
      .catch(() => null)
      .finally(() => coordinateInflight.delete(id));
    coordinateInflight.set(id, pending);
  }
  return pending;
}

const EMPTY: AirfoilSummaryMap = {};

/** Airfoil summaries keyed by id; empty while loading (the engine then uses stated fallbacks). */
export function useAirfoils(): AirfoilSummaryMap {
  const [map, setMap] = useState<AirfoilSummaryMap>(() => summaries ?? EMPTY);
  useEffect(() => {
    if (summaries) return;
    let live = true;
    loadAirfoils()
      .then((loaded) => {
        if (live) setMap(loaded);
      })
      .catch((error: unknown) => {
        if (!isAbortError(error)) console.warn('Could not load the airfoil library', error);
      });
    return () => {
      live = false;
    };
  }, []);
  return map;
}

/** Coordinates for the given airfoil ids (missing ones are simply absent until loaded). */
export function useAirfoilCoordinates(ids: string[]): Record<string, [number, number][]> {
  const key = [...new Set(ids)].sort().join(',');
  const [version, setVersion] = useState(0);
  useEffect(() => {
    let live = true;
    const wanted = key ? key.split(',') : [];
    const missing = wanted.filter((id) => !coordinateCache.has(id));
    if (missing.length === 0) return;
    void Promise.all(missing.map(loadCoordinates)).then(() => {
      if (live) setVersion((n) => n + 1);
    });
    return () => {
      live = false;
    };
  }, [key]);
  return useMemo(() => {
    void version; // recompute when a load finishes
    const out: Record<string, [number, number][]> = {};
    for (const id of key ? key.split(',') : []) {
      const c = coordinateCache.get(id);
      if (c) out[id] = c;
    }
    return out;
  }, [key, version]);
}
