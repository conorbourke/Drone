/**
 * Keyboard-accessible action menu: a trigger button (aria-haspopup) and a popup with
 * role="menu". Arrow keys move, Home/End jump, Escape and Tab close, Enter/Space activate,
 * and clicking outside closes. Focus returns to the trigger on close.
 */
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from 'react';

export interface MenuItem {
  label: ReactNode;
  onSelect: () => void;
  testId?: string;
  /** Styles the item as destructive. */
  danger?: boolean;
  disabled?: boolean;
}

interface MenuProps {
  /** Accessible name of the trigger. */
  label: string;
  items: MenuItem[];
  testId?: string;
  /** Trigger content; defaults to a vertical ellipsis. */
  children?: ReactNode;
  className?: string;
}

export function Menu({ label, items, testId, children, className }: MenuProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const close = useCallback((restoreFocus = true) => {
    setOpen(false);
    if (restoreFocus) triggerRef.current?.focus();
  }, []);

  const openMenu = () => {
    const enabled = items.findIndex((item) => !item.disabled);
    setActiveIndex(enabled === -1 ? 0 : enabled);
    setOpen(true);
  };

  useEffect(() => {
    if (!open) return;
    const frame = requestAnimationFrame(() => itemRefs.current[activeIndex]?.focus());
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!listRef.current?.contains(target) && !triggerRef.current?.contains(target)) {
        setOpen(false);
      }
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener('pointerdown', onPointerDown);
    };
    // Only re-run when the menu opens or closes; activeIndex changes are handled by moveTo().
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const moveTo = (index: number) => {
    const count = items.length;
    if (count === 0) return;
    let next = ((index % count) + count) % count;
    for (let i = 0; i < count && items[next]?.disabled; i += 1) {
      next = (next + 1) % count;
    }
    setActiveIndex(next);
    itemRefs.current[next]?.focus();
  };

  const onMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        moveTo(activeIndex + 1);
        break;
      case 'ArrowUp':
        event.preventDefault();
        moveTo(activeIndex - 1);
        break;
      case 'Home':
        event.preventDefault();
        moveTo(0);
        break;
      case 'End':
        event.preventDefault();
        moveTo(items.length - 1);
        break;
      case 'Escape':
        event.preventDefault();
        close();
        break;
      case 'Tab':
        close(false);
        break;
      default:
        break;
    }
  };

  const onTriggerKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      openMenu();
    }
  };

  return (
    <div className={`menu${className ? ` ${className}` : ''}`}>
      <button
        ref={triggerRef}
        type="button"
        className="button button-ghost menu-trigger"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        data-testid={testId}
        onClick={() => (open ? close(false) : openMenu())}
        onKeyDown={onTriggerKeyDown}
      >
        {children ?? <span aria-hidden="true">{'⋮'}</span>}
      </button>
      {open ? (
        <div ref={listRef} id={id} role="menu" aria-label={label} className="menu-popup" onKeyDown={onMenuKeyDown}>
          {items.map((item, index) => (
            <button
              key={index}
              ref={(element) => {
                itemRefs.current[index] = element;
              }}
              type="button"
              role="menuitem"
              tabIndex={index === activeIndex ? 0 : -1}
              className={`menu-item${item.danger ? ' menu-item-danger' : ''}`}
              data-testid={item.testId}
              disabled={item.disabled}
              onClick={() => {
                close();
                item.onSelect();
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
