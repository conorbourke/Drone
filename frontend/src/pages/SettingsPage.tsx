/**
 * Settings: printer envelope, mass limits and check thresholds (each with its description
 * and source from the API), backups with "Back up now" and downloads, and system info.
 */
import { useCallback, useEffect, useId, useState } from 'react';
import { Link } from 'react-router';
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

/** Display name for a path: the API's label, else the caller's fallback, else the key itself. */
function labelFor(meta: Meta, path: string, fallback?: string): string {
  return meta[path]?.label || fallback || humanizeKey(path);
}

/** Explain text for an entry: its description, then the source as a second paragraph. */
function explainText(entry: SettingsMetaEntry): string {
  return [entry.description, entry.source ? `Source: ${entry.source}` : ''].filter(Boolean).join('\n\n');
}

function SettingRow({
  path,
  meta,
  label,
  unit,
  children,
  error,
  className,
  isDefault,
}: {
  path: string;
  meta: Meta;
  /** Fallback title when the API has no label for the path. */
  label?: string;
  unit?: string | null;
  children: ReactNode;
  error?: string | null;
  /** Extra class for layout variants. */
  className?: string;
  /** Overrides the Default/Changed pill for rows that aggregate several paths; null hides it. */
  isDefault?: boolean | null;
}) {
  const entry = meta[path];
  const title = labelFor(meta, path, label);
  const unitText = unit === undefined ? unitForKey(path) : unit;
  const defaultState = isDefault === undefined ? (entry ? entry.is_default : null) : isDefault;
  return (
    <div className={`setting-row${className ? ` ${className}` : ''}`} data-testid={`setting-row-${path}`}>
      <div>
        <div className="setting-label">
          <span>
            {title}
            {unitText ? <span className="muted"> ({unitText})</span> : null}
          </span>
          {defaultState === null ? null : defaultState ? (
            <StatusPill tone="neutral" title="Using the shipped default">
              Default
            </StatusPill>
          ) : (
            <StatusPill tone="info" title="Changed from the shipped default">
              Changed
            </StatusPill>
          )}
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

/**
 * Number input that keeps partial text locally and propagates only finite numbers. While the
 * text is empty or not a number it reports that through onInvalid so the page can block saving;
 * on blur such text is normalised back to the stored value.
 */
function NumberInput({
  value,
  onChange,
  onInvalid,
  label,
  step = 'any',
  invalid,
  testId,
}: {
  value: number;
  onChange: (value: number) => void;
  /** Called when the text starts or stops being empty / not a number. */
  onInvalid: (invalid: boolean) => void;
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
        const nextBad = next.trim() === '' || !Number.isFinite(parsed);
        if (nextBad !== bad) onInvalid(nextBad);
        if (!nextBad) onChange(parsed);
      }}
      onBlur={() => {
        if (bad) {
          setText(String(value));
          onInvalid(false);
        }
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
  'manoeuvre_load_factor',
  'structural_safety_factor',
  'transition_thrust_margin_min',
];

const ANALYSIS_KEYS: Array<keyof Settings['analysis']> = ['ncrit'];

const LIMIT_KEYS: Array<keyof Settings['limits']> = ['warn_mtow_kg', 'design_mtow_kg', 'legal_mtow_kg'];

/** Section names for group-level 422 locations such as ["body", "limits"]. */
const GROUP_LABELS: Record<string, string> = {
  printer: '3D printer',
  'printer.build_volume_mm': 'Build volume',
  'printer.usable_envelope_mm': 'Usable envelope',
  limits: 'Mass limits',
  checks: 'Check thresholds',
  analysis: 'Analysis',
  units: 'Units',
};

function groupLabel(path: string): string {
  return GROUP_LABELS[path] ?? humanizeKey(path);
}

/** Dotted paths of every scalar below a value: "limits" → ["limits.design_mtow_kg", ...]. */
function leafPaths(value: unknown, prefix: string): string[] {
  if (typeof value !== 'object' || value === null) return [prefix];
  return Object.entries(value).flatMap(([key, child]) => leafPaths(child, prefix ? `${prefix}.${key}` : key));
}

const INVALID_NUMBER = 'Enter a number';

export function SettingsPage() {
  const toast = useToast();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [meta, setMeta] = useState<Meta>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  /** Inputs flagged by a group-level server error (the message is in the banner). */
  const [highlighted, setHighlighted] = useState<Record<string, true>>({});
  const [generalErrors, setGeneralErrors] = useState<string[]>([]);
  /** Server notes about the stored settings, e.g. values reset to defaults. */
  const [warnings, setWarnings] = useState<string[]>([]);
  /** Number inputs whose text is currently empty or not a number; saving is blocked meanwhile. */
  const [invalidNumbers, setInvalidNumbers] = useState<Record<string, true>>({});

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
        setWarnings(response.warnings ?? []);
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

  const setNumberInvalid = (path: string, invalid: boolean) => {
    setInvalidNumbers((current) => {
      if (!!current[path] === invalid) return current;
      const next = { ...current };
      if (invalid) next[path] = true;
      else delete next[path];
      return next;
    });
  };

  /** Inline message for a row: the server's, or the local one while its text is not a number. */
  const rowError = (path: string): string | null => fieldErrors[path] ?? (invalidNumbers[path] ? INVALID_NUMBER : null);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!settings) return;
    // Empty or non-numeric text never reaches the settings object; do not save the stale value.
    if (Object.keys(invalidNumbers).length > 0) return;
    setSaving(true);
    setFieldErrors({});
    setHighlighted({});
    setGeneralErrors([]);
    try {
      const response = await api<SettingsResponse>('/api/settings', { method: 'PUT', body: settings });
      setSettings(response.settings);
      setMeta(response.meta ?? {});
      setWarnings(response.warnings ?? []);
      setDirty(false);
      toast.success('Settings saved');
    } catch (caught) {
      if (isAuthError(caught)) return;
      if (caught instanceof ApiError && caught.status === 422) {
        const perField: Record<string, string> = {};
        const flagged: Record<string, true> = {};
        const general: string[] = [];
        for (const field of caught.fields) {
          const path = fieldErrorPath(field);
          const target = path ? getAtPath(settings, path) : undefined;
          if (target !== undefined && typeof target !== 'object') {
            perField[path] = field.msg;
          } else {
            // A group-level error (e.g. loc ["body", "limits"]): highlight every input in the
            // group and explain it once in the banner under a readable section name.
            if (target !== undefined) for (const leaf of leafPaths(target, path)) flagged[leaf] = true;
            general.push(path ? `${groupLabel(path)}: ${field.msg}` : field.msg);
          }
        }
        if (caught.fields.length === 0) general.push(caught.detail);
        setFieldErrors(perField);
        setHighlighted(flagged);
        setGeneralErrors(general);
        toast.error(
          general.length > 0
            ? 'Settings were not saved. See the message below.'
            : 'Settings were not saved. Check the highlighted values.',
        );
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
    const axes = ['x', 'y', 'z'] as const;
    // The API describes each axis separately; the row's pill summarises all three.
    const known = axes.map((axis) => meta[`${path}.${axis}`]).filter((entry) => entry !== undefined);
    const isDefault = known.length > 0 ? known.every((entry) => entry.is_default) : null;
    return (
      <SettingRow
        path={path}
        meta={meta}
        label={label}
        unit="mm"
        error={fieldErrors[path] ?? null}
        className="setting-row-envelope"
        isDefault={isDefault}
      >
        <div className="envelope-grid">
          {axes.map((axis) => {
            const axisPath = `${path}.${axis}`;
            const entry = meta[axisPath];
            const axisLabel = labelFor(meta, axisPath, `${label} ${axis.toUpperCase()}`);
            const axisError = rowError(axisPath);
            return (
              <div key={axis}>
                <div className="envelope-axis">
                  <span className="envelope-axis-label" aria-hidden="true">
                    {axis.toUpperCase()}
                  </span>
                  <NumberInput
                    label={`${axisLabel} (mm)`}
                    value={value[axis]}
                    onChange={(next) => update(axisPath, next)}
                    onInvalid={(invalid) => setNumberInvalid(axisPath, invalid)}
                    invalid={!!axisError || !!highlighted[axisPath]}
                    testId={`setting-${axisPath}`}
                  />
                  {entry?.description ? <Explain text={explainText(entry)} label={axisLabel} /> : null}
                </div>
                {axisError ? (
                  <p className="field-error" role="alert">
                    {axisError}
                  </p>
                ) : null}
              </div>
            );
          })}
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
          {warnings.length > 0 ? (
            <div className="banner banner-warn" role="status" data-testid="settings-warnings">
              <div>
                <div className="banner-title">Some settings need attention</div>
                <ul className="small">
                  {warnings.map((message, index) => (
                    <li key={index}>{message}</li>
                  ))}
                </ul>
              </div>
            </div>
          ) : null}

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
                aria-label={labelFor(meta, 'printer.name', 'Printer name')}
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
                <SettingRow key={key} path={path} meta={meta} error={rowError(path)}>
                  <NumberInput
                    label={`${labelFor(meta, path)} (kg)`}
                    value={settings.limits[key]}
                    onChange={(next) => update(path, next)}
                    onInvalid={(invalid) => setNumberInvalid(path, invalid)}
                    step={0.1}
                    invalid={!!rowError(path) || !!highlighted[path]}
                    testId={`setting-${path}`}
                  />
                </SettingRow>
              );
            })}
          </section>

          <section className="card" aria-labelledby="checks-heading">
            <div className="card-header">
              <h2 id="checks-heading">Check thresholds</h2>
            </div>
            <p className="card-note">
              Pass/warn/fail limits the full analysis applies. Each one lists where the default comes from; every check in
              an analysis names the threshold it used.
            </p>
            {CHECK_KEYS.map((key) => {
              const path = `checks.${key}`;
              return (
                <SettingRow key={key} path={path} meta={meta} unit={null} error={rowError(path)}>
                  <NumberInput
                    label={labelFor(meta, path)}
                    value={settings.checks[key]}
                    onChange={(next) => update(path, next)}
                    onInvalid={(invalid) => setNumberInvalid(path, invalid)}
                    step={0.01}
                    invalid={!!rowError(path) || !!highlighted[path]}
                    testId={`setting-${path}`}
                  />
                </SettingRow>
              );
            })}
          </section>

          <section className="card" aria-labelledby="analysis-settings-heading">
            <div className="card-header">
              <h2 id="analysis-settings-heading">Analysis</h2>
              <Link to="/validation" className="button button-ghost button-sm" data-testid="settings-validation-link">
                Validation report
              </Link>
            </div>
            <p className="card-note">
              Options for the full server analysis. The validation report shows how the engine compares with textbook
              results, wind-tunnel data and published aircraft.
            </p>
            {ANALYSIS_KEYS.map((key) => {
              const path = `analysis.${key}`;
              return (
                <SettingRow key={key} path={path} meta={meta} unit={null} error={rowError(path)}>
                  <NumberInput
                    label={labelFor(meta, path)}
                    value={settings.analysis?.[key] ?? 9}
                    onChange={(next) => update(path, next)}
                    onInvalid={(invalid) => setNumberInvalid(path, invalid)}
                    step={0.5}
                    invalid={!!rowError(path) || !!highlighted[path]}
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
              <input
                className="input"
                aria-label={labelFor(meta, 'units.system', 'Unit system')}
                value="Metric (mm, g, kg, m/s, W, Wh, €)"
                readOnly
              />
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
