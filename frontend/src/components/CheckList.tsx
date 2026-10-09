/**
 * Pass/warn/fail list for server checks: icon + label (never colour alone), the plain message,
 * and the threshold with where it comes from (settings meta or the mission).
 */
import type { AnalysisCheck } from '../api/analysis';
import { thresholdText } from '../lib/analysis';
import { StatusBadge } from '../panels/EstimatesPanel';

const LEVEL_ORDER: Record<string, number> = { fail: 0, warn: 1, ok: 2, info: 3 };

function CheckItem({ c }: { c: AnalysisCheck }) {
  const threshold = thresholdText(c.threshold, c.unit ?? '');
  return (
    <li className={`status-item status-${c.level}`} data-testid="analysis-check" data-check-key={c.key} data-level={c.level}>
      <div className="status-item-head">
        <StatusBadge level={c.level} />
        <span className="status-label">{c.label}</span>
      </div>
      <p className="status-message small">{c.message}</p>
      {threshold || c.threshold_source ? (
        <p className="check-threshold small muted">
          {threshold ? (
            <>
              Threshold: <strong>{threshold}</strong>.{' '}
            </>
          ) : null}
          {c.threshold_source ? <span className="faint">{c.threshold_source}</span> : null}
        </p>
      ) : null}
    </li>
  );
}

/**
 * Failing and warning checks first and always open; passing ones (and notes) folded under
 * "N checks pass" so the problems stand out. Every check stays in the DOM.
 */
export function CheckList({ checks, testId = 'analysis-checks' }: { checks: AnalysisCheck[]; testId?: string }) {
  const sorted = [...checks].sort((a, b) => (LEVEL_ORDER[a.level] ?? 9) - (LEVEL_ORDER[b.level] ?? 9));
  const problems = sorted.filter((c) => c.level === 'fail' || c.level === 'warn');
  const passing = sorted.filter((c) => c.level !== 'fail' && c.level !== 'warn');
  return (
    <div className="check-list" data-testid={testId}>
      {problems.length ? (
        <ul className="status-list">
          {problems.map((c) => (
            <CheckItem key={c.key} c={c} />
          ))}
        </ul>
      ) : null}
      {passing.length ? (
        <details className="breakdown check-pass-group" open={problems.length === 0 && passing.length <= 4}>
          <summary data-testid={`${testId}-passing`}>
            {passing.length} {passing.length === 1 ? 'check passes' : 'checks pass'}
          </summary>
          <ul className="status-list">
            {passing.map((c) => (
              <CheckItem key={c.key} c={c} />
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
