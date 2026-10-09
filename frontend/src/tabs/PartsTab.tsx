/**
 * Parts tab (Phase 4): the recommended parts list for the draft or a saved version, grouped by
 * system, with supplier links, prices, flags, the reasoning and Replace / Unlock per role; the
 * running totals against the prototype budget; the upgrades worth paying for; supplier price
 * checks (per part and for the whole list); and the full parts catalogue underneath.
 *
 * When the stored picks are out of date (stored.in_sync false) the list is recomputed once on
 * open, so the selection the Tier 1 estimates and the analysis use matches what is shown.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { isAbortError, isAuthError, errorMessage } from '../api/client';
import {
  getPart,
  getPartsList,
  listParts,
  recomputePartsList,
  refreshAllListings,
  refreshPartListings,
  setRolePart,
  unlockRole,
  type PartsList,
  type PartsListAlternative,
  type PartsListRole,
  type PartsSource,
  type RefreshAllResult,
} from '../api/partsList';
import type { Part } from '../api/types';
import { Explain } from '../components/Explain';
import { StatusPill } from '../components/StatusPill';
import { useToast } from '../components/Toast';
import { formatDateTime, formatEur, formatMassG } from '../lib/format';
import { groupBySystem, refreshActive, refreshErrorMessage, systemLabel } from '../lib/partsList';
import { useWorkspace } from '../lib/workspace';
import { PartLine, type RefreshState } from './parts/PartLine';
import { PartsCatalogue } from './parts/PartsCatalogue';
import { PartsTotals, PartsUpgrades } from './parts/PartsTotals';
import { ReplaceDialog } from './parts/ReplaceDialog';

/** How often a running supplier lookup is polled. */
const POLL_MS = 3000;

type Phase = 'loading' | 'updating' | 'ready' | 'error';

function refreshStateOf(part: Part): RefreshState {
  return {
    status: part.listings_refresh_status ?? null,
    message: part.listings_refresh_message ?? null,
    at: part.listings_refreshed_at ?? null,
    error: null,
  };
}

export function PartsTab() {
  const workspace = useWorkspace();
  const { projectId, versions, flushDraft, reloadPartsSelection } = workspace;
  const toast = useToast();
  const [source, setSource] = useState<PartsSource>('draft');
  const [list, setList] = useState<PartsList | null>(null);
  const [phase, setPhase] = useState<Phase>('loading');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  const [replacing, setReplacing] = useState<PartsListRole | null>(null);
  const [replaceError, setReplaceError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState<Record<number, RefreshState>>({});
  const [refreshAll, setRefreshAll] = useState<{ result: RefreshAllResult | null; error: string | null } | null>(null);
  const [refreshingAll, setRefreshingAll] = useState(false);
  /** Sources already recomputed automatically in this visit (once per source). */
  const autoRecomputed = useRef(new Set<string>());

  const sourceKey = String(source);

  /** After a change to the draft's stored picks, the Design tab's Tier 1 masses follow. */
  const afterChange = useCallback(
    (next: PartsList) => {
      setList(next);
      if (next.source.kind === 'draft') void reloadPartsSelection();
    },
    [reloadPartsSelection],
  );

  // Load the list; recompute once when the stored picks are out of date.
  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    (async () => {
      try {
        if (source === 'draft') await flushDraft();
        if (cancelled) return;
        const loaded = await getPartsList(projectId, source, controller.signal);
        if (cancelled) return;
        setList(loaded);
        setLoadError(null);
        if (!loaded.stored.in_sync && loaded.catalogue_size > 0 && !autoRecomputed.current.has(sourceKey)) {
          autoRecomputed.current.add(sourceKey);
          setPhase('updating');
          const fresh = await recomputePartsList(projectId, source, controller.signal);
          if (cancelled) return;
          afterChange(fresh);
        }
        setPhase('ready');
      } catch (caught) {
        if (cancelled || isAbortError(caught) || isAuthError(caught)) return;
        setLoadError(errorMessage(caught));
        setPhase('error');
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [projectId, source, sourceKey, attempt, flushDraft, afterChange]);

  // Refresh states of every part (a lookup may still be running from an earlier visit).
  useEffect(() => {
    const controller = new AbortController();
    listParts(controller.signal)
      .then((parts) => {
        setRefresh((current) => {
          const next = { ...current };
          for (const p of parts) if (!next[p.id]) next[p.id] = refreshStateOf(p);
          return next;
        });
      })
      .catch(() => {
        // Informational only: the lines still work without it.
      });
    return () => controller.abort();
  }, []);

  const reloadList = useCallback(async () => {
    try {
      setList(await getPartsList(projectId, source));
    } catch (caught) {
      if (!isAuthError(caught)) toast.error(`Could not reload the parts list: ${errorMessage(caught)}`);
    }
  }, [projectId, source, toast]);

  // Poll every running lookup every 3 s until it is done, refused or failed; then reload the list.
  const activeIds = useMemo(
    () =>
      Object.entries(refresh)
        .filter(([, state]) => refreshActive(state.status))
        .map(([id]) => Number(id))
        .sort((a, b) => a - b),
    [refresh],
  );
  const activeKey = activeIds.join(',');
  useEffect(() => {
    if (!activeKey) return;
    const ids = activeKey.split(',').map(Number);
    let stopped = false;
    const timer = setInterval(() => {
      void Promise.all(ids.map((id) => getPart(id).catch(() => null))).then((parts) => {
        if (stopped) return;
        setRefresh((current) => {
          const next = { ...current };
          for (const p of parts) if (p) next[p.id] = refreshStateOf(p);
          return next;
        });
        if (parts.some((p) => p && !refreshActive(p.listings_refresh_status))) void reloadList();
      });
    }, POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [activeKey, reloadList]);

  const recompute = async () => {
    setBusy(true);
    setPhase('updating');
    try {
      afterChange(await recomputePartsList(projectId, source));
      toast.success('Parts list updated.');
    } catch (caught) {
      if (!isAuthError(caught)) toast.error(`Could not update the parts list: ${errorMessage(caught)}`);
    } finally {
      setPhase('ready');
      setBusy(false);
    }
  };

  const choose = async (role: PartsListRole, alt: PartsListAlternative) => {
    setBusy(true);
    setReplaceError(null);
    try {
      const next = await setRolePart(projectId, source, role.role, {
        part_id: alt.part.id,
        ...(alt.quantity !== undefined ? { quantity: alt.quantity } : {}),
        locked: true,
      });
      afterChange(next);
      setReplacing(null);
      toast.success(`${role.label}: ${alt.part.manufacturer} ${alt.part.model} chosen and locked.`);
    } catch (caught) {
      if (!isAuthError(caught)) setReplaceError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  const unlock = async (role: PartsListRole) => {
    setBusy(true);
    try {
      afterChange(await unlockRole(projectId, source, role.role));
      toast.success(`${role.label} is back in the engine's hands.`);
    } catch (caught) {
      if (!isAuthError(caught)) toast.error(`Could not unlock ${role.label}: ${errorMessage(caught)}`);
    } finally {
      setBusy(false);
    }
  };

  const refreshPart = async (partId: number) => {
    setRefresh((current) => ({ ...current, [partId]: { ...(current[partId] ?? { status: null, message: null, at: null }), error: null } }));
    try {
      const { part } = await refreshPartListings(partId);
      setRefresh((current) => ({ ...current, [partId]: refreshStateOf(part) }));
    } catch (caught) {
      if (isAuthError(caught)) return;
      const message = refreshErrorMessage(caught);
      setRefresh((current) => ({
        ...current,
        [partId]: { ...(current[partId] ?? { status: null, message: null, at: null }), error: message },
      }));
    }
  };

  const refreshEverything = async () => {
    setRefreshingAll(true);
    try {
      const result = await refreshAllListings(projectId, source);
      setRefreshAll({ result, error: null });
      setRefresh((current) => {
        const next = { ...current };
        for (const q of result.queued) next[q.part_id] = { status: 'queued', message: 'Waiting for the worker.', at: new Date().toISOString(), error: null };
        return next;
      });
    } catch (caught) {
      if (!isAuthError(caught)) setRefreshAll({ result: null, error: refreshErrorMessage(caught) });
    } finally {
      setRefreshingAll(false);
    }
  };

  const groups = list ? groupBySystem(list.roles) : [];
  const updating = phase === 'updating';

  return (
    <div className="stack parts-tab">
      {list ? <PartsTotals list={list} /> : null}

      <section className="card" data-testid="parts-list" aria-labelledby="parts-list-title" aria-busy={phase !== 'ready'}>
        <div className="card-header">
          <h2 id="parts-list-title">Parts list</h2>
          <span className="spacer" />
          <label className="parts-source">
            <span className="small muted">For</span>
            <select
              className="input"
              value={sourceKey}
              onChange={(event) => {
                setPhase('loading');
                setList(null);
                setSource(event.target.value === 'draft' ? 'draft' : Number(event.target.value));
              }}
              data-testid="parts-source"
              aria-label="Parts list for"
            >
              <option value="draft">the draft</option>
              {(versions ?? []).map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.number} {v.name}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="button button-sm" onClick={() => void recompute()} disabled={busy || updating || phase === 'loading'} data-testid="parts-recompute">
            Recompute
          </button>
          <Explain
            label="Recompute"
            text="Runs the parts selection again for the current design: unlocked roles get the engine's best pick, locked roles keep your choice. It happens by itself when the design has changed since the list was stored."
          />
        </div>
        <p className="card-note">
          Real parts picked for this design by the engine, with Irish and UK shops. Every number comes from the catalogue;
          specifications are <strong>unverified</strong> until someone checks them on the manufacturer page.
          {list?.stored.stored_at ? <> Stored {formatDateTime(list.stored.stored_at)}.</> : null}
        </p>

        {updating ? (
          <div className="analysis-progress parts-updating" role="status" data-testid="parts-updating">
            <span className="progress-indeterminate" aria-hidden="true" />
            <span className="small">Updating the parts list…</span>
          </div>
        ) : null}

        {phase === 'error' ? (
          <div className="banner banner-error" role="alert" data-testid="parts-error">
            <div>
              <div className="banner-title">Could not load the parts list</div>
              <div className="small">{loadError}</div>
            </div>
            <button
              type="button"
              className="button button-sm"
              onClick={() => {
                setPhase('loading');
                setAttempt((n) => n + 1);
              }}
            >
              Try again
            </button>
          </div>
        ) : null}

        {!list && phase === 'loading' ? <p className="loading small">Loading the parts list…</p> : null}

        {list && list.catalogue_size === 0 ? (
          <p className="small" data-testid="parts-empty-catalogue">
            The parts catalogue is empty, so nothing can be recommended. Add parts to the catalogue first.
          </p>
        ) : null}

        {list ? (
          <>
            {list.unfilled.length > 0 ? (
              <div className="banner banner-warn" role="status" data-testid="parts-unfilled-summary">
                <div>
                  <div className="banner-title">
                    {list.unfilled.length === 1 ? 'One role could not be filled' : `${list.unfilled.length} roles could not be filled`}
                  </div>
                  <ul className="small">
                    {list.unfilled.map((u) => (
                      <li key={u.role}>
                        <strong>{u.label}:</strong> {u.reason}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            ) : null}

            <div className="parts-refresh-all row">
              <button
                type="button"
                className="button button-sm"
                onClick={() => void refreshEverything()}
                disabled={refreshingAll || activeIds.length > 0}
                data-testid="parts-refresh-all"
              >
                Check all prices
              </button>
              <span className="small muted">
                {activeIds.length > 0
                  ? `Checking ${activeIds.length} ${activeIds.length === 1 ? 'part' : 'parts'}; this page updates every few seconds.`
                  : 'Looks up current Irish and UK prices for every part in the list (each part once an hour).'}
              </span>
            </div>
            {refreshAll ? (
              <p className={`small ${refreshAll.error ? 'form-error' : 'muted'}`} role="status" data-testid="parts-refresh-all-message">
                {refreshAll.error ?? refreshAll.result?.message}
                {refreshAll.result && refreshAll.result.skipped.length > 0
                  ? ` Skipped: ${refreshAll.result.skipped.map((s) => s.name).join(', ')} (checked within the last hour).`
                  : ''}
              </p>
            ) : null}

            <div className={`parts-groups${updating ? ' parts-stale' : ''}`}>
              {groups.map((group) => {
                const lines = group.roles;
                const mass = lines.reduce((s, r) => s + (r.line_mass_g ?? 0), 0);
                const cost = lines.reduce((s, r) => s + (r.line_price_eur ?? 0), 0);
                return (
                  <section key={group.system} className="parts-system" data-testid="parts-system" data-system={group.system} aria-label={systemLabel(group.system, list.system_labels)}>
                    <h3 className="parts-system-title">
                      <span>{systemLabel(group.system, list.system_labels)}</span>
                      <span className="small muted">
                        {formatMassG(mass)} · {formatEur(cost)}
                      </span>
                    </h3>
                    {lines.map((role) => (
                      <PartLine
                        key={role.role}
                        role={role}
                        refresh={role.part ? refresh[role.part.id] : undefined}
                        busy={busy || updating}
                        onReplace={(r) => {
                          setReplaceError(null);
                          setReplacing(r);
                        }}
                        onUnlock={(r) => void unlock(r)}
                        onRefresh={(id) => void refreshPart(id)}
                      />
                    ))}
                  </section>
                );
              })}
              <section className="parts-system" data-testid="parts-system" data-system="consumables" aria-label={systemLabel('consumables', list.system_labels)}>
                <h3 className="parts-system-title">
                  <span>{systemLabel('consumables', list.system_labels)}</span>
                  <span className="small muted">
                    {formatMassG(list.consumables.mass_g)} · {formatEur(list.consumables.cost_eur)}
                  </span>
                </h3>
                <article className="parts-line" data-testid="parts-consumables">
                  <div className="parts-line-head">
                    <div className="parts-line-ident">
                      <div className="parts-line-role">{list.consumables.label}</div>
                      <div className="small muted">Estimated: not individual catalogue parts.</div>
                    </div>
                    <dl className="parts-line-figures">
                      <div>
                        <dt>
                          Mass <Explain label="consumables mass" text={list.consumables.mass_source} />
                        </dt>
                        <dd>{formatMassG(list.consumables.mass_g)}</dd>
                      </div>
                      <div>
                        <dt>
                          Cost <Explain label="consumables cost" text={list.consumables.cost_source} />
                        </dt>
                        <dd>{formatEur(list.consumables.cost_eur)}</dd>
                      </div>
                    </dl>
                  </div>
                  <details className="parts-consumable-items small">
                    <summary>What is included</summary>
                    <ul>
                      {list.consumables.items.map((item) => (
                        <li key={item.label}>
                          {item.label}: {formatEur(item.cost_eur)}
                        </li>
                      ))}
                    </ul>
                  </details>
                </article>
              </section>
            </div>
            {list.source.kind === 'version' ? (
              <p className="small muted">
                <StatusPill tone="info">v{list.source.version_number}</StatusPill> Changes here apply to this saved version only.
              </p>
            ) : null}
          </>
        ) : null}
      </section>

      {list ? <PartsUpgrades list={list} /> : null}

      <PartsCatalogue />

      <ReplaceDialog
        role={replacing}
        busy={busy}
        error={replaceError}
        onChoose={(role, alt) => void choose(role, alt)}
        onClose={() => setReplacing(null)}
      />
    </div>
  );
}
