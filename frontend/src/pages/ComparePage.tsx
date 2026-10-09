/**
 * Version comparison (/projects/:id/compare?versions=3,5): top and side outlines of two or
 * three saved versions overlaid in distinct colours (and dash patterns) with a legend, and a
 * table of key numbers from the Tier 1 engine with the differences from the first version
 * highlighted.
 */
import { useEffect, useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router';
import { useAirfoils } from '../api/airfoils';
import { api, errorMessage, isAbortError, isAuthError } from '../api/client';
import type { DesignVersion, Project, Settings, SettingsResponse, VersionSummary } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { Explain } from '../components/Explain';
import { AppShell } from '../layout/AppShell';
import { buildGeometry, estimate, LAYOUT_LABELS, type Estimates, type Geometry, type Quantity } from '../engine';
import { boundsOf, padBounds, pointsAttr, unionBounds, viewShapes, type View } from '../lib/drawing';
import { explainQuantity, formatQ } from '../panels/EstimatesPanel';

const DASHES = ['', '10 5', '3 4'];

export function parseVersionNumbers(raw: string | null): number[] {
  if (!raw) return [];
  const list = raw
    .split(',')
    .map((s) => Number(s.trim()))
    .filter((n) => Number.isInteger(n) && n > 0);
  return [...new Set(list)].slice(0, 3);
}

interface Loaded {
  project: Project;
  versions: DesignVersion[];
  missing: number[];
  settings: Settings;
}

interface Row {
  key: string;
  label: string;
  unit: string;
  digits?: number;
  get: (e: Estimates, v: DesignVersion) => Quantity | number | string | null;
}

const ROWS: Row[] = [
  { key: 'layout', label: 'Layout', unit: '', get: (_e, v) => LAYOUT_LABELS[v.parameters.layout] ?? v.parameters.layout },
  { key: 'span', label: 'Wingspan', unit: 'mm', get: (_e, v) => v.parameters.wing.span_mm },
  { key: 'wing_area', label: 'Wing area', unit: 'm²', digits: 3, get: (e) => e.aero?.wing_area ?? null },
  { key: 'aspect_ratio', label: 'Aspect ratio', unit: '', digits: 1, get: (e) => e.aero?.aspect_ratio ?? null },
  { key: 'mass', label: 'Take-off mass (heaviest camera)', unit: 'kg', get: (e) => e.mass?.takeoff_max_payload ?? null },
  { key: 'battery', label: 'Battery mass', unit: 'kg', get: (e) => e.mass?.battery ?? null },
  { key: 'static_margin_max', label: 'Static margin, heaviest camera', unit: '% MAC', get: (e) => e.balance?.static_margin_max_payload ?? null },
  { key: 'static_margin_min', label: 'Static margin, lightest camera', unit: '% MAC', get: (e) => e.balance?.static_margin_min_payload ?? null },
  { key: 'wing_loading', label: 'Wing loading', unit: 'kg/m²', get: (e) => e.aero?.wing_loading ?? null },
  { key: 'stall_speed', label: 'Stall speed', unit: 'm/s', get: (e) => e.aero?.stall_speed ?? null },
  { key: 'cruise_to_stall', label: 'Cruise / stall', unit: '', get: (e) => e.aero?.cruise_to_stall ?? null },
  { key: 'lift_to_drag', label: 'Lift-to-drag', unit: '', digits: 1, get: (e) => e.aero?.lift_to_drag ?? null },
  { key: 'cruise_power', label: 'Cruise power', unit: 'W', get: (e) => e.performance?.cruise_power ?? null },
  { key: 'hover_power', label: 'Hover power', unit: 'W', get: (e) => e.performance?.hover_power ?? null },
  { key: 'endurance', label: 'Wing-flight endurance', unit: 'min', get: (e) => e.performance?.endurance_cruise ?? null },
  { key: 'range', label: 'Range (still air)', unit: 'km', get: (e) => e.performance?.range ?? null },
];

function valueOf(x: Quantity | number | string | null): number | string | null {
  if (x === null) return null;
  if (typeof x === 'object') return x.value;
  return x;
}

export function ComparePage() {
  const params = useParams<{ id: string }>();
  const projectId = Number(params.id);
  const [search] = useSearchParams();
  const numbers = useMemo(() => parseVersionNumbers(search.get('versions')), [search]);
  const numbersKey = numbers.join(',');
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const airfoils = useAirfoils();

  useEffect(() => {
    const controller = new AbortController();
    const signal = controller.signal;
    const wanted = numbersKey ? numbersKey.split(',').map(Number) : [];
    (async () => {
      const [project, list, settings] = await Promise.all([
        api<Project>(`/api/projects/${projectId}`, { signal }),
        api<VersionSummary[]>(`/api/projects/${projectId}/versions`, { signal }),
        api<SettingsResponse>('/api/settings', { signal }),
      ]);
      const found = wanted.map((n) => list.find((v) => v.number === n)).filter((v): v is VersionSummary => !!v);
      const docs = await Promise.all(found.map((v) => api<DesignVersion>(`/api/versions/${v.id}`, { signal })));
      setLoaded({
        project,
        versions: docs,
        missing: wanted.filter((n) => !found.some((v) => v.number === n)),
        settings: settings.settings,
      });
      setError(null);
    })().catch((caught: unknown) => {
      if (isAbortError(caught) || isAuthError(caught)) return;
      setError(errorMessage(caught));
    });
    return () => controller.abort();
  }, [projectId, numbersKey]);

  const computed = useMemo(() => {
    if (!loaded) return [];
    return loaded.versions.map((v) => ({
      version: v,
      geometry: buildGeometry(v.parameters, { airfoils }),
      estimates: estimate({ parameters: v.parameters, mission: v.mission, settings: loaded.settings, airfoils, partsMasses: v.parts_selection?.masses_g ?? null }),
    }));
  }, [loaded, airfoils]);

  const back = (
    <Link to={`/projects/${projectId}?tab=design`} className="button button-sm" data-testid="compare-back">
      ← Back to the project
    </Link>
  );

  if (error) {
    return (
      <AppShell narrow>
        <EmptyState title="Could not load the comparison" description={error} action={back} />
      </AppShell>
    );
  }
  if (!loaded) {
    return (
      <AppShell title="Compare versions">
        <div className="skeleton" aria-busy="true" aria-label="Loading the comparison" />
      </AppShell>
    );
  }
  if (computed.length < 2) {
    return (
      <AppShell title={loaded.project.name} narrow>
        <EmptyState
          title="Choose two or three versions"
          description={
            loaded.missing.length
              ? `Version ${loaded.missing.map((n) => `v${n}`).join(', ')} no longer exists. Tick two or three versions in the Versions panel and press Compare.`
              : 'Tick two or three versions in the Versions panel and press Compare.'
          }
          action={back}
        />
      </AppShell>
    );
  }

  return (
    <AppShell title={loaded.project.name}>
      <div className="stack" data-testid="compare-view">
        <div className="row row-between">
          <h1>Compare versions</h1>
          {back}
        </div>
        {loaded.missing.length ? (
          <p className="small muted">Not found (deleted?): {loaded.missing.map((n) => `v${n}`).join(', ')}.</p>
        ) : null}
        <ul className="compare-legend" data-testid="compare-legend" aria-label="Legend">
          {computed.map((c, i) => (
            <li key={c.version.id} className={`series-${i + 1}`}>
              <svg width="36" height="12" aria-hidden="true">
                <line x1="2" x2="34" y1="6" y2="6" className="legend-line" strokeDasharray={DASHES[i]} />
              </svg>
              <strong>v{c.version.number}</strong> {c.version.name}
            </li>
          ))}
        </ul>
        <div className="compare-views">
          <Overlay view="top" items={computed} />
          <Overlay view="side" items={computed} />
        </div>
        <section className="card" aria-labelledby="compare-table-heading">
          <div className="card-header">
            <h2 id="compare-table-heading">Key numbers</h2>
          </div>
          <p className="card-note">
            From the Tier 1 estimate of each version with its own mission. Differences from v{computed[0].version.number}{' '}
            are highlighted.
          </p>
          <div className="table-scroll">
            <table className="table compare-table" data-testid="compare-table">
              <thead>
                <tr>
                  <th scope="col">Quantity</th>
                  {computed.map((c, i) => (
                    <th key={c.version.id} scope="col" className={`num series-${i + 1}`}>
                      <span className="series-swatch" aria-hidden="true" /> v{c.version.number}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ROWS.map((row) => {
                  const cells = computed.map((c) => row.get(c.estimates, c.version));
                  const base = valueOf(cells[0]);
                  const firstQ = cells.find((x): x is Quantity => typeof x === 'object' && x !== null);
                  return (
                    <tr key={row.key} data-row={row.key}>
                      <th scope="row">
                        {row.label}
                        {firstQ ? <Explain label={row.label} text={explainQuantity(firstQ, row.digits)} /> : null}
                      </th>
                      {cells.map((cell, i) => {
                        const v = valueOf(cell);
                        let text = '–';
                        let delta: string | null = null;
                        let differs = false;
                        if (typeof v === 'number') {
                          text = `${formatQ(v, row.unit, row.digits)}${row.unit ? ` ${row.unit}` : ''}`;
                          if (i > 0 && typeof base === 'number' && Number.isFinite(v) && Number.isFinite(base)) {
                            const diff = v - base;
                            const rel = Math.abs(base) > 1e-9 ? Math.abs(diff / base) : Math.abs(diff);
                            differs = rel > 0.005;
                            if (differs) delta = `${diff > 0 ? '+' : '−'}${formatQ(Math.abs(diff), row.unit, row.digits)}`;
                          }
                        } else if (typeof v === 'string') {
                          text = v;
                          differs = i > 0 && v !== base;
                        }
                        return (
                          <td key={i} className={`num${differs ? ' is-diff' : ''}`} data-diff={differs || undefined}>
                            {text}
                            {delta ? <span className="delta"> ({delta})</span> : null}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </AppShell>
  );
}

function Overlay({ view, items }: { view: View; items: { version: DesignVersion; geometry: Geometry }[] }) {
  const layers = items.map((item) => viewShapes(view, item.geometry).filter((s) => s.role !== 'motor' && s.role !== 'gear'));
  const all = unionBounds(layers.map((shapes) => boundsOf(shapes)));
  const size = Math.max(all.maxU - all.minU, all.maxV - all.minV, 100);
  const box = padBounds(all, size * 0.04);
  return (
    <figure className="compare-overlay" data-testid={`compare-${view}`}>
      <figcaption className="drawing-title">{view === 'top' ? 'Top view' : 'Side view'}</figcaption>
      <svg
        viewBox={`${box.minU} ${box.minV} ${box.maxU - box.minU} ${box.maxV - box.minV}`}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={`${view === 'top' ? 'Top' : 'Side'} outlines of ${items.map((i) => `v${i.version.number}`).join(', ')} overlaid`}
      >
        {layers.map((shapes, i) => (
          <g key={items[i].version.id} className={`overlay-layer series-${i + 1}`} data-version-number={items[i].version.number} strokeDasharray={DASHES[i] || undefined}>
            {shapes.map((s, k) =>
              s.open ? (
                <polyline key={k} points={pointsAttr(s.points)} className={`ov ov-${s.role}`} />
              ) : (
                <polygon key={k} points={pointsAttr(s.points)} className={`ov ov-${s.role}`} />
              ),
            )}
          </g>
        ))}
      </svg>
    </figure>
  );
}
