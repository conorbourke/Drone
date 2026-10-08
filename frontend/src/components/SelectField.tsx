/**
 * Enum input built from schema metadata. Option notes (such as the rear_tilt caveat) are
 * available through Explain and shown inline when the noted option is selected.
 */
import { useId } from 'react';
import type { FieldMeta } from '../api/types';
import { Explain } from './Explain';

interface SelectFieldProps {
  path: string;
  meta: FieldMeta;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  error?: string | null;
}

export function SelectField({ path, meta, value, onChange, disabled, error }: SelectFieldProps) {
  const id = useId();
  const options = meta.enum ?? [];
  const selected = options.find((option) => option.value === value);
  const notes = options.filter((option) => option.note).map((option) => `${option.label}: ${option.note}`);
  const explain = [meta.description, ...notes].filter(Boolean).join('\n\n');
  const describedBy = [selected?.note ? `${id}-note` : null, error ? `${id}-error` : null]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={`field${error ? ' field-has-error' : ''}`}>
      <div className="field-head">
        <label htmlFor={id} className="field-label">
          {meta.label}
        </label>
        <Explain text={explain} label={meta.label} />
      </div>
      <div className="field-control">
        <select
          id={id}
          data-testid={`field-${path}`}
          name={path}
          value={value}
          disabled={disabled}
          aria-describedby={describedBy || undefined}
          aria-invalid={error ? true : undefined}
          onChange={(event) => onChange(event.target.value)}
        >
          {!selected && value ? <option value={value}>{value}</option> : null}
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>
      {selected?.note ? (
        <p id={`${id}-note`} className="field-note">
          <span className="field-note-text">{selected.note}</span>
          <Explain text={selected.note} label={`${meta.label} note`} />
        </p>
      ) : null}
      {error ? (
        <p id={`${id}-error`} className="field-error">
          {error}
        </p>
      ) : null}
    </div>
  );
}
