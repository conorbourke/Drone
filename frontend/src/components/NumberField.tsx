/**
 * Unit-aware number input with a label and an Explain button. The label, unit, explanation
 * and limits come from the field's schema metadata; nothing is hard-coded here.
 *
 * Typing is held in local text so partial input such as "1." is not rejected; only valid
 * numbers are propagated to the draft. On blur the text is normalised to the stored value.
 */
import { useId, useState } from 'react';
import type { FieldMeta } from '../api/types';
import type { SliderRange } from '../lib/ranges';
import { Explain } from './Explain';

interface NumberFieldProps {
  /** Dotted path of the field; the input carries data-testid="field-<path>". */
  path: string;
  meta: FieldMeta;
  value: number;
  onChange: (value: number) => void;
  disabled?: boolean;
  /** Inline validation message from the server, if any. */
  error?: string | null;
  /** Show a slider under the box over this range (data-testid="slider-<path>"). */
  slider?: SliderRange | null;
}

function toText(value: number): string {
  return Number.isFinite(value) ? String(value) : '';
}

function explainText(meta: FieldMeta): string {
  const parts = [meta.description];
  const limits: string[] = [];
  if (meta.min !== undefined) limits.push(`at least ${meta.min}`);
  if (meta.max !== undefined) limits.push(`at most ${meta.max}`);
  if (limits.length) parts.push(`Allowed range: ${limits.join(', ')}${meta.unit ? ` ${meta.unit}` : ''}.`);
  return parts.filter(Boolean).join('\n\n');
}

export function NumberField({ path, meta, value, onChange, disabled, error, slider }: NumberFieldProps) {
  const id = useId();
  const [text, setText] = useState(() => toText(value));
  const [lastValue, setLastValue] = useState(value);

  // Adopt external changes (restore, reload) unless the text already represents the value.
  if (!Object.is(value, lastValue)) {
    setLastValue(value);
    if (Number(text) !== value || text.trim() === '') setText(toText(value));
  }

  const invalid = text.trim() === '' || !Number.isFinite(Number(text));
  const isInteger = meta.type === 'integer';
  const describedBy = error ? `${id}-error` : undefined;

  return (
    <div className={`field${error ? ' field-has-error' : ''}`}>
      <div className="field-head">
        <label htmlFor={id} className="field-label">
          {meta.label}
        </label>
        <Explain text={explainText(meta)} label={meta.label} />
      </div>
      <div className="field-control">
        <input
          id={id}
          data-testid={`field-${path}`}
          name={path}
          type="number"
          inputMode="decimal"
          step={isInteger ? 1 : 'any'}
          min={meta.min}
          max={meta.max}
          value={text}
          disabled={disabled}
          aria-invalid={invalid || !!error ? true : undefined}
          aria-describedby={describedBy}
          onChange={(event) => {
            const next = event.target.value;
            setText(next);
            if (next.trim() === '') return;
            const parsed = Number(next);
            if (Number.isFinite(parsed)) onChange(isInteger ? Math.round(parsed) : parsed);
          }}
          onBlur={() => {
            if (invalid) setText(toText(value));
          }}
        />
        {meta.unit ? <span className="field-unit">{meta.unit}</span> : null}
      </div>
      {slider ? (
        <input
          type="range"
          className="field-slider"
          data-testid={`slider-${path}`}
          aria-label={`${meta.label} slider`}
          min={slider.min}
          max={slider.max}
          step={slider.step}
          value={Number.isFinite(value) ? value : slider.min}
          disabled={disabled}
          onChange={(event) => {
            const parsed = Number(event.target.value);
            if (Number.isFinite(parsed)) onChange(isInteger ? Math.round(parsed) : parsed);
          }}
        />
      ) : null}
      {error ? (
        <p id={`${id}-error`} className="field-error">
          {error}
        </p>
      ) : null}
    </div>
  );
}
