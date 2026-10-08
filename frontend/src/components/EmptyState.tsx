/** Friendly placeholder for lists with nothing in them and for later-phase features. */
import type { ReactNode } from 'react';

interface EmptyStateProps {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  /** Shown as a pill above the title, e.g. "Phase 2". */
  badge?: string;
  testId?: string;
  className?: string;
}

export function EmptyState({ title, description, action, badge, testId, className }: EmptyStateProps) {
  return (
    <div className={`empty-state${className ? ` ${className}` : ''}`} data-testid={testId}>
      {badge ? <span className="pill pill-info empty-state-badge">{badge}</span> : null}
      <h3 className="empty-state-title">{title}</h3>
      {description ? <div className="empty-state-description">{description}</div> : null}
      {action ? <div className="empty-state-action">{action}</div> : null}
    </div>
  );
}
