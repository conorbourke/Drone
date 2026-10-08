/**
 * A small "?" button that reveals a plain-language explanation on hover, focus or click.
 * The popover is positioned in the viewport and clamped to the 16 px gutters so it never
 * causes horizontal scroll on a phone.
 */
import { useCallback, useEffect, useId, useRef, useState } from 'react';

interface ExplainProps {
  /** The explanation. Blank lines separate paragraphs. */
  text: string;
  /** What is being explained, for the button's accessible name. */
  label?: string;
  className?: string;
}

const GUTTER = 16;
const WIDTH = 300;

export function Explain({ text, label, className }: ExplainProps) {
  const id = useId();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [position, setPosition] = useState<{ top: number; left: number; width: number } | null>(null);

  const place = useCallback(() => {
    const button = buttonRef.current;
    if (!button) return;
    const rect = button.getBoundingClientRect();
    const viewportWidth = window.innerWidth;
    const width = Math.min(WIDTH, viewportWidth - GUTTER * 2);
    const left = Math.min(Math.max(rect.left, GUTTER), viewportWidth - width - GUTTER);
    setPosition({ top: rect.bottom + 6, left, width });
  }, []);

  const show = useCallback(() => {
    place();
    setOpen(true);
  }, [place]);

  const hide = useCallback(() => {
    setOpen(false);
    setPinned(false);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onScroll = () => hide();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') hide();
    };
    const onPointerDown = (event: PointerEvent) => {
      if (!buttonRef.current?.contains(event.target as Node)) hide();
    };
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onScroll);
    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onPointerDown);
    return () => {
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onScroll);
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('pointerdown', onPointerDown);
    };
  }, [open, hide]);

  if (!text) return null;
  const paragraphs = text.split(/\n{2,}/).map((p) => p.trim()).filter(Boolean);

  return (
    <span className={`explain${className ? ` ${className}` : ''}`}>
      <button
        ref={buttonRef}
        type="button"
        className="explain-button"
        aria-label={label ? `Explain ${label}` : 'Explain'}
        aria-expanded={open}
        aria-describedby={open ? id : undefined}
        onMouseEnter={() => {
          if (!pinned) show();
        }}
        onMouseLeave={() => {
          if (!pinned) hide();
        }}
        onFocus={show}
        onBlur={() => {
          if (!pinned) hide();
        }}
        onClick={() => {
          if (open && pinned) {
            hide();
          } else {
            show();
            setPinned(true);
          }
        }}
      >
        ?
      </button>
      {open && position && (
        <span
          id={id}
          role="tooltip"
          className="explain-popover"
          style={{ top: position.top, left: position.left, width: position.width }}
        >
          {paragraphs.map((paragraph, index) => (
            <span key={index} className="explain-paragraph">
              {paragraph}
            </span>
          ))}
        </span>
      )}
    </span>
  );
}
