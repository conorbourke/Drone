/**
 * Inputs tab: the mission form, the layout choice, and the Phase 2 placeholder for
 * reference images. Every label, unit and explanation comes from /api/schema/*.
 */
import { metaFor, useSchemas } from '../api/schema';
import type { DraftDocument, FieldMeta, SchemaMap } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { NumberField } from '../components/NumberField';
import { SelectField } from '../components/SelectField';
import { TextField } from '../components/TextField';
import { getAtPath } from '../lib/draft';

export interface TabProps {
  doc: DraftDocument;
  /** Update a dotted path on the draft document, e.g. "mission.cruise_speed_mps". */
  update: (path: string, value: unknown) => void;
  /** Server validation messages keyed by full dotted path ("mission.payload_max_g"). */
  fieldErrors: Record<string, string>;
}

/** Render one schema field by type. `root` is "mission" or "parameters"; `path` is the schema key. */
export function SchemaField({
  root,
  path,
  meta,
  doc,
  update,
  fieldErrors,
}: {
  root: 'mission' | 'parameters';
  path: string;
  meta: FieldMeta;
  doc: DraftDocument;
  update: TabProps['update'];
  fieldErrors: Record<string, string>;
}) {
  const fullPath = `${root}.${path}`;
  const raw = getAtPath(doc, fullPath);
  const error = fieldErrors[fullPath] ?? null;
  if (meta.enum && meta.enum.length > 0) {
    return (
      <SelectField
        path={path}
        meta={meta}
        value={typeof raw === 'string' ? raw : ''}
        onChange={(value) => update(fullPath, value)}
        error={error}
      />
    );
  }
  if (meta.type === 'boolean') {
    return null; // No boolean fields in the Phase 1 documents.
  }
  if (meta.type === 'string') {
    return (
      <TextField
        path={path}
        meta={meta}
        value={typeof raw === 'string' ? raw : ''}
        onChange={(value) => update(fullPath, value)}
        error={error}
      />
    );
  }
  return (
    <NumberField
      path={path}
      meta={meta}
      value={typeof raw === 'number' ? raw : Number.NaN}
      onChange={(value) => update(fullPath, value)}
      error={error}
    />
  );
}

/** Keys of a schema that are real inputs (schema_version is bookkeeping). */
export function inputKeys(schema: SchemaMap): string[] {
  return Object.keys(schema).filter((key) => key !== 'schema_version');
}

export function SchemaLoading({ status, error, retry }: { status: string; error: string | null; retry: () => void }) {
  if (status === 'error') {
    return (
      <EmptyState
        title="Could not load the field definitions"
        description={error ?? 'The server did not answer.'}
        action={
          <button type="button" className="button" onClick={retry}>
            Try again
          </button>
        }
      />
    );
  }
  return <div className="skeleton" aria-busy="true" aria-label="Loading" />;
}

export function InputsTab({ doc, update, fieldErrors }: TabProps) {
  const { status, schemas, error, retry } = useSchemas();

  if (!schemas) {
    return <SchemaLoading status={status} error={error} retry={retry} />;
  }

  const missionKeys = inputKeys(schemas.mission);
  const layoutMeta = metaFor(schemas.design, 'layout');

  return (
    <div className="stack">
      <section className="card" aria-labelledby="mission-heading">
        <div className="card-header">
          <h2 id="mission-heading">Mission</h2>
        </div>
        <p className="card-note">
          What the aircraft must do. These targets drive every later estimate; the engine arrives in Phases 2
          and 3. Hover or tap the <strong>?</strong> beside a value for an explanation.
        </p>
        {schemas.notes.defaults ? (
          <p className="card-note" data-testid="defaults-note">
            {schemas.notes.defaults}
          </p>
        ) : null}
        <div className="field-grid">
          {missionKeys.map((key) => (
            <SchemaField
              key={key}
              root="mission"
              path={key}
              meta={metaFor(schemas.mission, key)}
              doc={doc}
              update={update}
              fieldErrors={fieldErrors}
            />
          ))}
        </div>
        {fieldErrors['mission'] ? (
          <p className="form-error" role="alert">
            {fieldErrors['mission']}
          </p>
        ) : null}
      </section>

      <section className="card" aria-labelledby="layout-heading">
        <div className="card-header">
          <h2 id="layout-heading">Layout</h2>
        </div>
        <p className="card-note">
          How the aircraft transitions between hover and wing flight. Only layouts ArduPilot can fly are offered.
        </p>
        <div className="field-grid">
          <SchemaField
            root="parameters"
            path="layout"
            meta={layoutMeta}
            doc={doc}
            update={update}
            fieldErrors={fieldErrors}
          />
        </div>
      </section>

      <EmptyState
        badge="Phase 2"
        title="Reference images"
        description="Upload three or four AI-generated renders from different angles and give one real dimension (for example the wingspan). Claude will read them and propose starting values for the design parameters. This arrives in Phase 2."
        testId="images-placeholder"
      />
    </div>
  );
}
