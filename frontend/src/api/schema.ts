/**
 * Loads the field metadata for mission and design documents (and the plain-language notes
 * shown beside them) once and shares it through a React context. Labels, units and
 * explanations come from here and nowhere else.
 */
import { createContext, createElement, useCallback, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { api, errorMessage, isAbortError } from './client';
import type { FieldMeta, SchemaMap, SchemaNotes } from './types';

export interface Schemas {
  mission: SchemaMap;
  design: SchemaMap;
  /** Notes such as "starting values, not an analysed design"; blank when the server has none. */
  notes: SchemaNotes;
}

type SchemaStatus = 'loading' | 'ready' | 'error';

export interface SchemaContextValue {
  status: SchemaStatus;
  schemas: Schemas | null;
  error: string | null;
  retry: () => void;
}

const SchemaContext = createContext<SchemaContextValue>({
  status: 'loading',
  schemas: null,
  error: null,
  retry: () => undefined,
});

// Module-level cache: the schemas are static for a deployment, so one load per page life is enough.
let cached: Schemas | null = null;
let inflight: Promise<Schemas> | null = null;

export function loadSchemas(signal?: AbortSignal): Promise<Schemas> {
  if (cached) return Promise.resolve(cached);
  if (!inflight) {
    inflight = Promise.all([
      api<SchemaMap>('/api/schema/mission', { signal }),
      api<SchemaMap>('/api/schema/design', { signal }),
      // The notes are informational: a server without them must not block editing.
      api<SchemaNotes>('/api/schema/notes', { signal }).catch((error: unknown): SchemaNotes => {
        if (isAbortError(error)) throw error;
        return { defaults: '' };
      }),
    ])
      .then(([mission, design, notes]) => {
        cached = { mission, design, notes };
        return cached;
      })
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

export function SchemaProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ status: SchemaStatus; schemas: Schemas | null; error: string | null }>(
    () => (cached ? { status: 'ready', schemas: cached, error: null } : { status: 'loading', schemas: null, error: null }),
  );
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (cached) return;
    const controller = new AbortController();
    loadSchemas(controller.signal)
      .then((schemas) => setState({ status: 'ready', schemas, error: null }))
      .catch((error: unknown) => {
        if (isAbortError(error)) return;
        setState({ status: 'error', schemas: null, error: errorMessage(error) });
      });
    return () => controller.abort();
  }, [attempt]);

  const retry = useCallback(() => {
    setState({ status: 'loading', schemas: null, error: null });
    setAttempt((n) => n + 1);
  }, []);

  return createElement(SchemaContext.Provider, { value: { ...state, retry } }, children);
}

export function useSchemas(): SchemaContextValue {
  return useContext(SchemaContext);
}

/** Turn "wing.root_chord_mm" into "Root chord" as a last-resort label. */
export function fallbackLabel(path: string): string {
  const last = path.split('.').pop() ?? path;
  const words = last.replace(/_(mm|m|g|kg|deg|mps|min|w|wh|eur|v|a|pct)$/i, '').split('_');
  const text = words.join(' ').trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : path;
}

/** Field metadata for a dotted path, with a safe fallback when the server omits it. */
export function metaFor(schema: SchemaMap | null | undefined, path: string): FieldMeta {
  const meta = schema?.[path];
  if (meta) return meta;
  return { label: fallbackLabel(path), unit: null, type: 'number', description: '' };
}

/** Dotted paths in a schema that start with the given group prefix, in server order. */
export function groupPaths(schema: SchemaMap, group: string): string[] {
  const prefix = `${group}.`;
  return Object.keys(schema).filter((key) => key.startsWith(prefix));
}
