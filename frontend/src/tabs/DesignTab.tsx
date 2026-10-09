/**
 * Design tab: the 3D view (lazy-loaded three.js) and the top, side and front drawings with
 * drag handles, the live estimates panel beside them, and the grouped parameter fields
 * (number box plus slider) driven by /api/schema/design. Every edit re-runs the Tier 1
 * engine synchronously, so the estimates update in the same frame.
 */
import { Component, lazy, Suspense, useCallback, useMemo } from 'react';
import type { ReactNode } from 'react';
import { useAirfoilCoordinates, useAirfoils } from '../api/airfoils';
import { groupPaths, metaFor, useSchemas } from '../api/schema';
import type { Settings } from '../api/types';
import { DesignDrawing } from '../components/DesignDrawing';
import { buildGeometry, estimate, withDefaults } from '../engine';
import { EstimatesPanel } from '../panels/EstimatesPanel';
import { SchemaField, SchemaLoading, type TabProps } from './InputsTab';

const Model3D = lazy(() => import('../components/Model3D'));

interface Group {
  key: string;
  label: string;
  note?: string;
}

const GROUPS: Group[] = [
  { key: 'wing', label: 'Wing' },
  { key: 'fuselage', label: 'Fuselage' },
  { key: 'booms', label: 'Booms' },
  { key: 'motors', label: 'Motors' },
  { key: 'propulsion', label: 'Propellers' },
  { key: 'tilt', label: 'Tilt mechanism', note: 'Used by the front-tilt and rear-tilt layouts.' },
  { key: 'pusher', label: 'Pusher motor', note: 'Used by the quad + pusher layout.' },
  { key: 'tail', label: 'Tail' },
  { key: 'nose_bay', label: 'Nose bay', note: 'Geometry only; the payload mass range is set in the mission.' },
  { key: 'battery', label: 'Battery' },
  { key: 'allowances', label: 'Allowances', note: 'Placeholders for parts chosen in Phase 4.' },
  { key: 'landing_gear', label: 'Landing gear' },
];

class ViewBoundary extends Component<{ children: ReactNode }, { error: boolean }> {
  override state = { error: false };
  static getDerivedStateFromError() {
    return { error: true };
  }
  override render() {
    if (this.state.error) {
      return (
        <div className="model3d model3d-placeholder" data-testid="view-3d">
          <p className="small muted">The 3D view could not be loaded. Reload the page to try again; the drawings below show the same design.</p>
        </div>
      );
    }
    return this.props.children;
  }
}

export interface DesignTabProps extends TabProps {
  /** Phase 1 settings document (limits and check thresholds); null while loading. */
  settings: Settings | null;
}

export function DesignTab({ doc, update, fieldErrors, settings }: DesignTabProps) {
  const { status, schemas, error, retry } = useSchemas();
  const airfoils = useAirfoils();
  const params = doc.parameters;
  const resolved = withDefaults(params);
  const coordinates = useAirfoilCoordinates([resolved.wing.airfoil, resolved.tail.airfoil]);

  const geometry = useMemo(() => buildGeometry(params, { airfoils, coordinates }), [params, airfoils, coordinates]);
  const estimates = useMemo(
    () => (settings ? estimate({ parameters: params, mission: doc.mission, settings, airfoils }) : null),
    [params, doc.mission, settings, airfoils],
  );

  const onEdit = useCallback(
    (updates: [string, number][]) => {
      for (const [path, value] of updates) update(`parameters.${path}`, value);
    },
    [update],
  );

  if (!schemas) {
    return <SchemaLoading status={status} error={error} retry={retry} />;
  }

  const layout = params.layout;
  const layoutMeta = metaFor(schemas.design, 'layout');
  const layoutLabel = layoutMeta.enum?.find((option) => option.value === layout)?.label ?? layout;
  const visibleGroups = GROUPS.filter((group) => {
    if (group.key === 'tilt') return layout !== 'quad_pusher';
    if (group.key === 'pusher') return layout === 'quad_pusher';
    return true;
  });
  const shownGroups = new Set(GROUPS.map((g) => g.key));
  const otherGroups = [...new Set(Object.keys(schemas.design).filter((k) => k.includes('.')).map((k) => k.split('.')[0]))].filter(
    (key) => !shownGroups.has(key),
  );

  return (
    <div className="stack">
      <p className="small muted">
        Layout: <strong>{layoutLabel}</strong> (change it on the Inputs tab). All lengths are in millimetres, angles
        in degrees. Drag the orange handles in the 3D view or the drawings; hold Shift to snap to 10 mm.
      </p>

      <div className="design-layout">
        <div className="design-views">
          <ViewBoundary>
            <Suspense
              fallback={
                <div className="model3d model3d-placeholder" data-testid="view-3d" aria-busy="true">
                  <p className="small muted">Loading the 3D view…</p>
                </div>
              }
            >
              <Model3D geometry={geometry} parameters={params} schema={schemas.design} onEdit={onEdit} />
            </Suspense>
          </ViewBoundary>
          <div className="drawings-grid">
            <DesignDrawing view="top" geometry={geometry} parameters={params} estimates={estimates} schema={schemas.design} onEdit={onEdit} />
            <DesignDrawing view="side" geometry={geometry} parameters={params} estimates={estimates} schema={schemas.design} onEdit={onEdit} />
            <DesignDrawing view="front" geometry={geometry} parameters={params} estimates={estimates} schema={schemas.design} onEdit={onEdit} />
          </div>
          <p className="drawing-legend small muted">
            <span>
              <span className="legend-cg legend-cg-heavy" aria-hidden="true" /> balance point, heaviest camera
            </span>
            <span>
              <span className="legend-cg" aria-hidden="true" /> lightest camera
            </span>
            <span>
              <span className="legend-tri" aria-hidden="true" /> neutral point
            </span>
            <span>
              <span className="legend-bay" aria-hidden="true" /> nose bay
            </span>
          </p>
          {geometry.statuses.length > 0 ? (
            <ul className="geometry-notes small">
              {geometry.statuses.map((s) => (
                <li key={s.key} className={`status-${s.level}`}>
                  <strong>{s.label}:</strong> {s.message}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        <div className="design-estimates">
          {estimates && settings ? (
            <EstimatesPanel estimates={estimates} settings={settings} />
          ) : (
            <div className="skeleton" aria-busy="true" aria-label="Loading estimates" data-testid="estimates-panel" />
          )}
        </div>
      </div>

      {schemas.notes.defaults ? (
        <p className="small muted" data-testid="defaults-note">
          {schemas.notes.defaults}
        </p>
      ) : null}

      {[...visibleGroups, ...otherGroups.map((key) => ({ key, label: key.replace(/_/g, ' ') }) as Group)].map((group) => {
        const paths = groupPaths(schemas.design, group.key);
        if (paths.length === 0) return null;
        return (
          <section key={group.key} className="card" aria-labelledby={`group-${group.key}`}>
            <div className="card-header">
              <h2 id={`group-${group.key}`}>{group.label}</h2>
            </div>
            {group.note ? <p className="card-note">{group.note}</p> : null}
            <div className="field-grid">
              {paths.map((path) => (
                <SchemaField
                  key={path}
                  root="parameters"
                  path={path}
                  meta={metaFor(schemas.design, path)}
                  doc={doc}
                  update={update}
                  fieldErrors={fieldErrors}
                />
              ))}
            </div>
            {fieldErrors[`parameters.${group.key}`] ? (
              <p className="form-error" role="alert">
                {fieldErrors[`parameters.${group.key}`]}
              </p>
            ) : null}
          </section>
        );
      })}

      {fieldErrors['parameters'] ? (
        <p className="form-error" role="alert">
          {fieldErrors['parameters']}
        </p>
      ) : null}
    </div>
  );
}
