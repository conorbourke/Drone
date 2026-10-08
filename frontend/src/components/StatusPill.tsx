/** Small coloured label for a state: Saved, Unverified, Default ... */
import type { ReactNode } from 'react';

export type PillTone = 'neutral' | 'ok' | 'warn' | 'error' | 'info';

interface StatusPillProps {
  tone?: PillTone;
  children: ReactNode;
  testId?: string;
  title?: string;
  className?: string;
}

export function StatusPill({ tone = 'neutral', children, testId, title, className }: StatusPillProps) {
  return (
    <span className={`pill pill-${tone}${className ? ` ${className}` : ''}`} data-testid={testId} title={title}>
      {children}
    </span>
  );
}
