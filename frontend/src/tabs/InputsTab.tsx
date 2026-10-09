/**
 * Inputs tab: the mission form, the layout choice, reference images with the Claude image
 * reading and its proposal review, and the layout comparison. Every label, unit and explanation comes from /api/schema/*.
 */
import { useAirfoils } from '../api/airfoils';
import { metaFor, useSchemas } from '../api/schema';
import type { DraftDocument, FieldMeta, SchemaMap, Settings } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { NumberField } from '../components/NumberField';
import { SelectField } from '../components/SelectField';
import { TextField } from '../components/TextField';
import { getAtPath } from '../lib/draft';
import { ImageReadingCard } from './inputs/ImageReading';
import { LayoutCompare } from './inputs/LayoutCompare';
import { ReferenceImages, useProjectImages } from './inputs/ReferenceImages';
import { sliderRange } from '../lib/ranges';

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
  if (path === 'wing.airfoil' || path === 'tail.airfoil') {
    return (
      <AirfoilField
        path={path}
        meta={meta}
        value={typeof raw === 'string' ? raw : ''}
        onChange={(value) => update(fullPath, value)}
        error={error}
      />
    );
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
  const value = typeof raw === 'number' ? raw : Number.NaN;
  return (
    <NumberField
      path={path}
      meta={meta}
      value={value}
      onChange={(next) => update(fullPath, next)}
      error={error}
      slider={sliderRange(path, meta, value)}
    />
  );
}

/** Airfoil choice from the library (GET /api/airfoils); a free text box until it loads. */
function AirfoilField({
  path,
  meta,
  value,
  onChange,
  error,
}: {
  path: string;
  meta: FieldMeta;
  value: string;
  onChange: (value: string) => void;
  error: string | null;
}) {
  const airfoils = useAirfoils();
  const use = path.startsWith('tail.') ? 'tail' : 'wing';
  const list = Object.values(airfoils).sort((a, b) => (a.use === use ? 0 : 1) - (b.use === use ? 0 : 1) || a.name.localeCompare(b.name));
  if (list.length === 0) {
    return <TextField path={path} meta={meta} value={value} onChange={onChange} error={error} />;
  }
  const chosen = airfoils[value];
  const withOptions: FieldMeta = {
    ...meta,
    // The chosen section's description goes into the explanation (option notes are for caveats).
    description: [meta.description, chosen ? `${chosen.name}: ${chosen.description} Source: ${chosen.source}.` : '']
      .filter(Boolean)
      .join('\n\n'),
    enum: list.map((a) => ({
      value: a.id,
      label: `${a.name} (${a.thickness_pct.toFixed(1)} %${a.use !== use ? `, ${a.use}` : ''})`,
    })),
  };
  return <SelectField path={path} meta={withOptions} value={value} onChange={onChange} error={error} />;
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

export interface InputsTabProps extends TabProps {
  projectId: number;
  /** Phase 1 settings document (limits and check thresholds); null while loading. */
  settings: Settings | null;
}

export function InputsTab({ doc, update, fieldErrors, projectId, settings }: InputsTabProps) {
  const { status, schemas, error, retry } = useSchemas();
  const airfoils = useAirfoils();
  const imagesState = useProjectImages(projectId);

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
          What the aircraft must do. These targets drive every estimate on the Design tab. Hover or tap the{' '}
          <strong>?</strong> beside a value for an explanation.
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

      <ReferenceImages
        projectId={projectId}
        images={imagesState.images}
        setImages={imagesState.setImages}
        loadError={imagesState.loadError}
        reload={() => imagesState.reload()}
      />

      <ImageReadingCard
        projectId={projectId}
        images={imagesState.images}
        doc={doc}
        update={update}
        schema={schemas.design}
      />

      <LayoutCompare doc={doc} settings={settings} airfoils={airfoils} update={update} />
    </div>
  );
}
