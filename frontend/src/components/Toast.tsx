/**
 * Toast notifications. `useToast()` gives `success`, `error` and `info`; messages appear in a
 * polite live region and dismiss themselves (errors stay longer and can be closed).
 */
import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router';

type Tone = 'success' | 'error' | 'info';

/** Optional link shown after the message, e.g. "Compare with v3". */
export interface ToastAction {
  label: string;
  to: string;
}

interface ToastItem {
  id: number;
  tone: Tone;
  message: string;
  action?: ToastAction;
}

export interface ToastApi {
  success: (message: string, action?: ToastAction) => void;
  error: (message: string, action?: ToastAction) => void;
  info: (message: string, action?: ToastAction) => void;
}

const ToastContext = createContext<ToastApi>({
  success: () => undefined,
  error: () => undefined,
  info: () => undefined,
});

const DURATION_MS: Record<Tone, number> = { success: 4000, info: 5000, error: 8000 };

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setItems((current) => current.filter((item) => item.id !== id));
  }, []);

  const push = useCallback(
    (tone: Tone, message: string, action?: ToastAction) => {
      const id = nextId.current;
      nextId.current += 1;
      setItems((current) => [...current.slice(-3), { id, tone, message, action }]);
      // A toast with a link stays long enough to reach it.
      setTimeout(() => dismiss(id), DURATION_MS[tone] + (action ? 6000 : 0));
    },
    [dismiss],
  );

  const api = useMemo<ToastApi>(
    () => ({
      success: (message, action) => push('success', message, action),
      error: (message, action) => push('error', message, action),
      info: (message, action) => push('info', message, action),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-region" aria-live="polite" aria-relevant="additions">
        {items.map((item) => (
          <div key={item.id} className={`toast toast-${item.tone}`} role={item.tone === 'error' ? 'alert' : 'status'}>
            <span className="toast-message">
              {item.message}
              {item.action ? (
                <>
                  {' '}
                  <Link to={item.action.to} className="toast-action" data-testid="toast-action" onClick={() => dismiss(item.id)}>
                    {item.action.label}
                  </Link>
                </>
              ) : null}
            </span>
            <button type="button" className="toast-close" aria-label="Dismiss notification" onClick={() => dismiss(item.id)}>
              {'×'}
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  return useContext(ToastContext);
}
