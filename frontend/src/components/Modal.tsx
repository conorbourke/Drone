/**
 * Accessible dialog on top of the native <dialog> element: focus is trapped, Escape closes,
 * the page behind is inert, and clicking the backdrop closes.
 */
import { useEffect, useId, useRef } from 'react';
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

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const onCancel = (event: Event) => {
      event.preventDefault();
      onClose();
    };
    dialog.addEventListener('cancel', onCancel);
    return () => dialog.removeEventListener('cancel', onCancel);
  }, [onClose]);

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
