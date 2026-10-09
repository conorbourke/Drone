/**
 * Accessible dialog on top of the native <dialog> element: focus is trapped, Escape closes,
 * the page behind is inert, and clicking the backdrop closes.
 */
import { useEffect, useId, useReducer, useRef } from 'react';
import type { ReactNode } from 'react';

interface ModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** Buttons rendered in the footer. */
  footer?: ReactNode;
  /** Extra class for width variants. */
  className?: string;
  /** Test id for the dialog element. */
  testId?: string;
}

export function Modal({ open, title, onClose, children, footer, className, testId }: ModalProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  // Forces a render when the browser closed the element by itself, so the sync below runs again.
  const [, rerender] = useReducer((count: number) => count + 1, 0);

  // Keep the element in step with the prop on every render: the browser can close a native
  // dialog on its own (Chromium does on a second Escape while the owner keeps it open), and
  // showModal() must then run again or the dialog could never be reopened.
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  });

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const onCancel = (event: Event) => {
      event.preventDefault();
      onClose();
    };
    // Fires whenever the element closes, including the close() requested above (open is false
    // then). Any other close is the browser's doing: tell the owner, then re-sync the element.
    const onNativeClose = () => {
      if (!open || dialog.open) return;
      onClose();
      rerender();
    };
    dialog.addEventListener('cancel', onCancel);
    dialog.addEventListener('close', onNativeClose);
    return () => {
      dialog.removeEventListener('cancel', onCancel);
      dialog.removeEventListener('close', onNativeClose);
    };
  }, [open, onClose]);

  return (
    <dialog
      ref={ref}
      className={`modal${className ? ` ${className}` : ''}`}
      aria-labelledby={titleId}
      data-testid={testId}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      {open ? (
        <div className="modal-body">
          <h2 id={titleId} className="modal-title">
            {title}
          </h2>
          <div className="modal-content">{children}</div>
          {footer ? <div className="modal-footer">{footer}</div> : null}
        </div>
      ) : null}
    </dialog>
  );
}
