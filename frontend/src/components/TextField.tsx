/** Plain text input with label and Explain, for string fields such as the airfoil id. */
import { useId } from 'react';
import type { FieldMeta } from '../api/types';
import { Explain } from './Explain';

interface TextFieldProps {
  path: string;
  meta: FieldMeta;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  error?: string | null;
}

export function TextField({ path, meta, value, onChange, disabled, error }: TextFieldProps) {
  const id = useId();
  return (
    <div className={`field${error ? ' field-has-error' : ''}`}>
      <div className="field-head">
        <label htmlFor={id} className="field-label">
          {meta.label}
        </label>
        <Explain text={meta.description} label={meta.label} />
      </div>
      <div className="field-control">
        <input
          id={id}
          data-testid={`field-${path}`}
          name={path}
          type="text"
          value={value}
          disabled={disabled}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
          onChange={(event) => onChange(event.target.value)}
        />
        {meta.unit ? <span className="field-unit">{meta.unit}</span> : null}
      </div>
      {error ? (
        <p id={`${id}-error`} className="field-error">
          {error}
        </p>
      ) : null}
    </div>
  );
}
