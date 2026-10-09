/**
 * Design tab: grouped parameter fields (wing, fuselage, booms, motors, tilt or pusher, tail,
 * nose bay, landing gear) driven by /api/schema/design, plus the Phase 2 placeholder for the
 * 3D view and drawings.
 */
import { groupPaths, metaFor, useSchemas } from '../api/schema';
import { EmptyState } from '../components/EmptyState';
import { SchemaField, SchemaLoading, type TabProps } from './InputsTab';

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
  { key: 'tilt', label: 'Tilt mechanism', note: 'Used by the front-tilt and rear-tilt layouts.' },
  { key: 'pusher', label: 'Pusher motor', note: 'Used by the quad + pusher layout.' },
  { key: 'tail', label: 'Tail' },
  { key: 'nose_bay', label: 'Nose bay', note: 'Geometry only; the payload mass range is set in the mission.' },
  { key: 'landing_gear', label: 'Landing gear' },
];

export function DesignTab({ doc, update, fieldErrors }: TabProps) {
  const { status, schemas, error, retry } = useSchemas();

  if (!schemas) {
    return <SchemaLoading status={status} error={error} retry={retry} />;
  }

  const layout = doc.parameters.layout;
  const layoutMeta = metaFor(schemas.design, 'layout');
  const layoutLabel = layoutMeta.enum?.find((option) => option.value === layout)?.label ?? layout;
  const visibleGroups = GROUPS.filter((group) => {
    if (group.key === 'tilt') return layout !== 'quad_pusher';
    if (group.key === 'pusher') return layout === 'quad_pusher';
    return true;
  });

  return (
    <div className="stack">
      <p className="small muted">
        Layout: <strong>{layoutLabel}</strong> (change it on the Inputs tab). All lengths are in millimetres, angles
        in degrees.
      </p>
      {schemas.notes.defaults ? (
        <p className="small muted" data-testid="defaults-note">
          {schemas.notes.defaults}
        </p>
      ) : null}

      {visibleGroups.map((group) => {
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

      <EmptyState
        badge="Phase 2"
        title="3D view and drawings"
        description="An editable 3D model with drag handles, plus top, side and front drawings, will appear here in Phase 2 together with the instant estimates (weight, balance, wing loading, stall speed, endurance)."
        testId="model-placeholder"
      />
    </div>
  );
}
