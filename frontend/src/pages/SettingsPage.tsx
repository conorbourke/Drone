/**
 * Settings: printer envelope, mass limits and check thresholds (each with its description
 * and source from the API), backups with "Back up now" and downloads, and system info.
 */
import { useCallback, useEffect, useId, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { ApiError, api, errorMessage, fieldErrorPath, isAbortError, isAuthError } from '../api/client';
import type { BackupEntry, Settings, SettingsMetaEntry, SettingsResponse, SystemInfo } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { Explain } from '../components/Explain';
import { StatusPill } from '../components/StatusPill';
import { useToast } from '../components/Toast';
import { AppShell } from '../layout/AppShell';
import { getAtPath, setAtPath } from '../lib/draft';
import { formatBytes, formatDateTime, humanizeKey, unitForKey } from '../lib/format';

type Meta = Record<string, SettingsMetaEntry>;

/** Metadata for a path, falling back to its parent (e.g. the envelope object for an axis). */
function metaFor(meta: Meta, path: string): SettingsMetaEntry | undefined {
  if (meta[path]) return meta[path];
  const parent = path.split('.').slice(0, -1).join('.');
  return parent ? meta[parent] : undefined;
}

function SettingRow({
  path,
  meta,
  label,
  unit,
  children,
  error,
}: {
  path: string;
  meta: Meta;
  label?: string;
  unit?: string | null;
  children: ReactNode;
  error?: string | null;
}) {
  const entry = metaFor(meta, path);
  const title = label ?? humanizeKey(path);
  const unitText = unit === undefined ? unitForKey(path) : unit;
  return (
    <div className="setting-row" data-testid={`setting-row-${path}`}>
      <div>
        <div className="setting-label">
          <span>
            {title}
            {unitText ? <span className="muted"> ({unitText})</span> : null}
          </span>
          {entry ? (
            entry.is_default ? (
              <StatusPill tone="neutral" title="Using the shipped default">
                Default
              </StatusPill>
            ) : (
              <StatusPill tone="info" title="Changed from the shipped default">
                Changed
              </StatusPill>
            )
          ) : null}
          {entry?.description ? <Explain text={entry.description} label={title} /> : null}
        </div>
        {entry?.description ? <p className="setting-description">{entry.description}</p> : null}
        {entry?.source ? <p className="setting-source">Source: {entry.source}</p> : null}
        {error ? (
          <p className="field-error" role="alert">
            {error}
          </p>
        ) : null}
      </div>
      <div>{children}</div>
    </div>
  );
}

function NumberInput({
  value,
  onChange,
  label,
  step = 'any',
  invalid,
  testId,
}: {
  value: number;
  onChange: (value: number) => void;
  label: string;
  step?: number | 'any';
  invalid?: boolean;
  testId?: string;
}) {
  const id = useId();
  const [text, setText] = useState(() => String(value));
  const [lastValue, setLastValue] = useState(value);
  if (!Object.is(value, lastValue)) {
    setLastValue(value);
    if (Number(text) !== value || text.trim() === '') setText(String(value));
  }
  const bad = text.trim() === '' || !Number.isFinite(Number(text));
  return (
    <input
      id={id}
      className="input"
      type="number"
      inputMode="decimal"
      step={step}
      aria-label={label}
      aria-invalid={bad || invalid ? true : undefined}
      data-testid={testId}
      value={text}
      onChange={(event) => {
        const next = event.target.value;
        setText(next);
        const parsed = Number(next);
        if (next.trim() !== '' && Number.isFinite(parsed)) onChange(parsed);
      }}
      onBlur={() => {
        if (bad) setText(String(value));
      }}
    />
  );
}

const CHECK_KEYS: Array<keyof Settings['checks']> = [
  'hover_thrust_to_weight_min',
  'static_margin_min',
  'static_margin_max',
  'cruise_to_stall_speed_ratio_min',
  'battery_reserve_fraction',
  'battery_current_max_fraction_of_rating',
];

const LIMIT_KEYS: Array<keyof Settings['limits']> = ['warn_mtow_kg', 'design_mtow_kg', 'legal_mtow_kg'];

export function SettingsPage() {
  const toast = useToast();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [meta, setMeta] = useState<Meta>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [generalErrors, setGeneralErrors] = useState<string[]>([]);

  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [backups, setBackups] = useState<BackupEntry[] | null>(null);
  const [backupsError, setBackupsError] = useState<string | null>(null);
  const [backingUp, setBackingUp] = useState(false);

  const loadBackups = useCallback(
    (signal?: AbortSignal) =>
      api<BackupEntry[]>('/api/system/backups', { signal })
        .then((list) => {
          setBackups(list);
          setBackupsError(null);
        })
        .catch((caught: unknown) => {
          if (isAbortError(caught) || isAuthError(caught)) return;
          setBackupsError(errorMessage(caught));
        }),
    [],
  );

  const loadInfo = useCallback(
    (signal?: AbortSignal) =>
      api<SystemInfo>('/api/system/info', { signal })
        .then((loaded) => setInfo(loaded))
        .catch(() => {
          // System info is informational; a failure here is not worth a toast.
        }),
    [],
  );

  useEffect(() => {
    const controller = new AbortController();
    api<SettingsResponse>('/api/settings', { signal: controller.signal })
      .then((response) => {
        setSettings(response.settings);
        setMeta(response.meta ?? {});
        setLoadError(null);
      })
      .catch((caught: unknown) => {
        if (isAbortError(caught) || isAuthError(caught)) return;
        setLoadError(errorMessage(caught));
      });
    void loadBackups(controller.signal);
    void loadInfo(controller.signal);
    return () => controller.abort();
  }, [loadBackups, loadInfo]);

  const update = (path: string, value: unknown) => {
    setSettings((current) => (current ? setAtPath(current, path, value) : current));
    setDirty(true);
  };

  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!settings) return;
    setSaving(true);
    setFieldErrors({});
    setGeneralErrors([]);
    try {
      const response = await api<SettingsResponse>('/api/settings', { method: 'PUT', body: settings });
      setSettings(response.settings);
      setMeta(response.meta ?? {});
      setDirty(false);
      toast.success('Settings saved');
    } catch (caught) {
      if (isAuthError(caught)) return;
      if (caught instanceof ApiError && caught.status === 422) {
        const perField: Record<string, string> = {};
        const general: string[] = [];
        for (const field of caught.fields) {
          const path = fieldErrorPath(field);
          if (path && getAtPath(settings, path) !== undefined && typeof getAtPath(settings, path) !== 'object') {
            perField[path] = field.msg;
          } else {
            general.push(path ? `${path}: ${field.msg}` : field.msg);
          }
        }
        if (caught.fields.length === 0) general.push(caught.detail);
        setFieldErrors(perField);
        setGeneralErrors(general);
        toast.error('Settings were not saved. Check the highlighted values.');
      } else {
        toast.error(`Could not save settings: ${errorMessage(caught)}`);
      }
    } finally {
      setSaving(false);
    }
  };

  const backupNow = async () => {
    setBackingUp(true);
    try {
      const entry = await api<BackupEntry>('/api/system/backups', { method: 'POST' });
      setBackups((current) => [entry, ...(current ?? []).filter((b) => b.name !== entry.name)]);
      toast.success(`Backup ${entry.name} created`);
      void loadInfo();
    } catch (caught) {
      if (!isAuthError(caught)) toast.error(`Backup failed: ${errorMessage(caught)}`);
    } finally {
      setBackingUp(false);
    }
  };

  const envelopeRow = (path: 'printer.build_volume_mm' | 'printer.usable_envelope_mm', label: string) => {
    if (!settings) return null;
    const value = getAtPath(settings, path) as Settings['printer']['build_volume_mm'];
    return (
      <SettingRow path={path} meta={meta} label={label} unit="mm" error={fieldErrors[path] ?? null}>
        <div className="envelope-grid">
          {(['x', 'y', 'z'] as const).map((axis) => (
            <div key={axis}>
              <NumberInput
                label={`${label} ${axis.toUpperCase()} (mm)`}
                value={value[axis]}
                onChange={(next) => update(`${path}.${axis}`, next)}
                invalid={!!fieldErrors[`${path}.${axis}`]}
                testId={`setting-${path}.${axis}`}
              />
              {fieldErrors[`${path}.${axis}`] ? <p className="field-error">{fieldErrors[`${path}.${axis}`]}</p> : null}
            </div>
          ))}
        </div>
      </SettingRow>
    );
  };

  return (
    <AppShell title="Settings" narrow>
      <div className="page-header">
        <h1>Settings</h1>
      </div>

      {loadError ? (
        <EmptyState title="Could not load settings" description={loadError} />
      ) : !settings ? (
        <div className="skeleton" aria-busy="true" aria-label="Loading settings" />
      ) : (
        <form className="stack" onSubmit={(event) => void save(event)} data-testid="settings-form">
          <section className="card" aria-labelledby="printer-heading">
            <div className="card-header">
              <h2 id="printer-heading">3D printer</h2>
            </div>
            <p className="card-note">
              Printed parts are split to fit the usable envelope, which must fit inside the build volume.
            </p>
            <SettingRow path="printer.name" meta={meta} label="Printer" unit={null}>
              <input
                className="input"
                aria-label="Printer name"
                data-testid="setting-printer.name"
                value={settings.printer.name}
                onChange={(event) => update('printer.name', event.target.value)}
              />
            </SettingRow>
            {envelopeRow('printer.build_volume_mm', 'Build volume')}
            {envelopeRow('printer.usable_envelope_mm', 'Usable envelope')}
          </section>

          <section className="card" aria-labelledby="limits-heading">
            <div className="card-header">
              <h2 id="limits-heading">Mass limits</h2>
            </div>
            <p className="card-note">
              The workspace warns from the warning mass and shows an error above the design limit. The legal limit
              for EU Open A3 is 25 kg.
            </p>
            {LIMIT_KEYS.map((key) => {
              const path = `limits.${key}`;
              return (
                <SettingRow key={key} path={path} meta={meta} error={fieldErrors[path] ?? null}>
                  <NumberInput
                    label={`${humanizeKey(key)} (kg)`}
                    value={settings.limits[key]}
                    onChange={(next) => update(path, next)}
                    step={0.1}
                    invalid={!!fieldErrors[path]}
                    testId={`setting-${path}`}
                  />
                </SettingRow>
              );
            })}
            {fieldErrors['limits'] ? (
              <p className="form-error" role="alert">
                {fieldErrors['limits']}
              </p>
            ) : null}
          </section>

          <section className="card" aria-labelledby="checks-heading">
            <div className="card-header">
              <h2 id="checks-heading">Check thresholds</h2>
              <StatusPill tone="info">Proposed; confirm in Phase 3</StatusPill>
            </div>
            <p className="card-note">
              Pass/warn/fail limits the Phase 3 analysis will apply. Each one lists where the default comes from.
            </p>
            {CHECK_KEYS.map((key) => {
              const path = `checks.${key}`;
              return (
                <SettingRow key={key} path={path} meta={meta} unit={null} error={fieldErrors[path] ?? null}>
                  <NumberInput
                    label={humanizeKey(key)}
                    value={settings.checks[key]}
                    onChange={(next) => update(path, next)}
                    step={0.01}
                    invalid={!!fieldErrors[path]}
                    testId={`setting-${path}`}
                  />
                </SettingRow>
              );
            })}
          </section>

          <section className="card" aria-labelledby="units-heading">
            <div className="card-header">
              <h2 id="units-heading">Units</h2>
            </div>
            <SettingRow path="units.system" meta={meta} label="Unit system" unit={null}>
              <input className="input" aria-label="Unit system" value="Metric (mm, g, kg, m/s, W, Wh, €)" readOnly />
            </SettingRow>
          </section>

          {generalErrors.length > 0 ? (
            <div className="banner banner-error" role="alert">
              <div>
                <div className="banner-title">Settings were not saved</div>
                <ul className="small">
                  {generalErrors.map((message, index) => (
                    <li key={index}>{message}</li>
                  ))}
                </ul>
              </div>
            </div>
          ) : null}

          <div className="form-actions">
            {dirty ? <span className="muted small">Unsaved changes</span> : null}
            <button type="submit" className="button button-primary" data-testid="settings-save" disabled={saving}>
              {saving ? 'Saving…' : 'Save settings'}
            </button>
          </div>
        </form>
      )}

      <section className="card" aria-labelledby="backups-heading" style={{ marginTop: 'var(--space-5)' }}>
        <div className="card-header">
          <h2 id="backups-heading">Backups</h2>
          <button
            type="button"
            className="button button-sm"
            data-testid="backup-now"
            onClick={() => void backupNow()}
            disabled={backingUp}
          >
            {backingUp ? 'Backing up…' : 'Back up now'}
          </button>
        </div>
        <p className="card-note">
          A copy of the database is taken daily and kept on the same disk as the app, which protects against
          mistakes, not disasters. Download a backup to keep an off-site copy. Disaster recovery uses the hosting
          provider's volume snapshots (see the deployment guide).
        </p>
        {backupsError ? (
          <p className="form-error" role="alert">
            Could not load backups: {backupsError}{' '}
            <button type="button" className="button button-sm" onClick={() => void loadBackups()}>
              Retry
            </button>
          </p>
        ) : backups === null ? (
          <p className="loading small">Loading backups…</p>
        ) : backups.length === 0 ? (
          <EmptyState title="No backups yet" description="The first backup runs shortly after the app starts, or press Back up now." />
        ) : (
          <div className="table-scroll">
            <table className="table" data-testid="backups-table">
              <thead>
                <tr>
                  <th scope="col">File</th>
                  <th scope="col">Created</th>
                  <th scope="col" className="num">
                    Size
                  </th>
                  <th scope="col">
                    <span className="visually-hidden">Download</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {backups.map((backup) => (
                  <tr key={backup.name} data-testid="backup-row">
                    <td className="mono">{backup.name}</td>
                    <td className="nowrap">{formatDateTime(backup.created_at)}</td>
                    <td className="num">{formatBytes(backup.size_bytes)}</td>
                    <td>
                      <a
                        className="button button-sm"
                        href={`/api/system/backups/${encodeURIComponent(backup.name)}`}
                        download={backup.name}
                      >
                        Download
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card" aria-labelledby="system-heading">
        <div className="card-header">
          <h2 id="system-heading">System</h2>
        </div>
        {info ? (
          <dl className="kv" data-testid="system-info">
            <dt>Version</dt>
            <dd className="mono">{info.version}</dd>
            <dt>Phase</dt>
            <dd>{info.phase}</dd>
            <dt>Environment</dt>
            <dd>{info.environment}</dd>
            <dt>Data directory</dt>
            <dd className="mono">{info.data_dir}</dd>
            <dt>Last backup</dt>
            <dd>{formatDateTime(info.backup.last_run_at)}</dd>
            <dt>Next backup</dt>
            <dd>{formatDateTime(info.backup.next_run_at)}</dd>
            <dt>Backups kept</dt>
            <dd>{info.backup.count}</dd>
          </dl>
        ) : (
          <p className="loading small">Loading system information…</p>
        )}
      </section>
    </AppShell>
  );
}
