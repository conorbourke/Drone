/**
 * "Replace" for one role of the parts list: the role's alternatives from the selection engine,
 * each feasible or not with the reason it lost and its numbers against the chosen part.
 * Choosing one stores it as the owner's (locked) choice for that role.
 */
import type { PartsListAlternative, PartsListRole } from '../../api/partsList';
import { Modal } from '../../components/Modal';
import { StatusPill } from '../../components/StatusPill';
import { formatEur, formatMassG } from '../../lib/format';
import { deltaTexts } from '../../lib/partsList';

interface ReplaceDialogProps {
  role: PartsListRole | null;
  busy: boolean;
  error: string | null;
  onChoose: (role: PartsListRole, alternative: PartsListAlternative) => void;
  onClose: () => void;
}

const TIER_LABEL: Record<string, string> = { cheaper: 'Cheaper option', premium: 'Premium option', alternative: 'Alternative' };

export function ReplaceDialog({ role, busy, error, onChoose, onClose }: ReplaceDialogProps) {
  const alternatives = role?.alternatives ?? [];
  return (
    <Modal
      open={role !== null}
      title={role ? `Replace: ${role.label}` : 'Replace'}
      onClose={onClose}
      className="modal-wide"
      testId="parts-replace-dialog"
      footer={
        <button type="button" className="button" onClick={onClose} data-testid="parts-replace-cancel">
          Cancel
        </button>
      }
    >
      {role ? (
        <div className="stack-sm">
          <p className="small muted">
            Now: <strong>{role.part ? `${role.part.manufacturer} ${role.part.model}` : 'nothing chosen'}</strong>
            {role.part ? ` · ${formatMassG(role.part.mass_g)} · ${formatEur(role.part.price_eur)} each` : ''}. Your choice is
            kept (locked) when the design changes; Unlock hands the role back to the engine. Mass and price changes are
            against the current choice.
          </p>
          {error ? (
            <p className="form-error" role="alert">
              {error}
            </p>
          ) : null}
          {alternatives.length === 0 ? (
            <p className="small" data-testid="parts-no-alternatives">
              The catalogue has no other part for this role that fits the design. Add one to the catalogue to see it here.
            </p>
          ) : (
            <ul className="alt-list">
              {alternatives.map((alt, index) => {
                const deltas = deltaTexts(alt.deltas);
                return (
                  <li
                    key={`${alt.part.id}-${alt.quantity ?? ''}-${index}`}
                    className={`alt-item${alt.feasible ? '' : ' alt-infeasible'}`}
                    data-testid="parts-alternative"
                    data-part-id={alt.part.id}
                    data-feasible={alt.feasible ? 'true' : 'false'}
                  >
                    <div className="alt-main">
                      <div className="alt-title">
                        <span className="alt-name">
                          {alt.part.manufacturer} {alt.part.model}
                        </span>
                        {alt.feasible ? (
                          <StatusPill tone="ok">
                            <span aria-hidden="true">✓</span> Meets the requirements
                          </StatusPill>
                        ) : (
                          <StatusPill tone="error">
                            <span aria-hidden="true">✕</span> Does not meet the requirements
                          </StatusPill>
                        )}
                        {alt.tier ? <StatusPill tone="neutral">{TIER_LABEL[alt.tier] ?? alt.tier}</StatusPill> : null}
                      </div>
                      {alt.label ? <div className="small muted">{alt.label}</div> : null}
                      <p className="alt-reason small">{alt.reason_lost}</p>
                      {deltas.length > 0 ? (
                        <ul className="alt-deltas small" aria-label="Compared with the current choice">
                          {deltas.map((d) => (
                            <li key={d}>{d}</li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                    <div className="alt-action">
                      <span className="small muted nowrap">
                        {formatMassG(alt.part.mass_g)} · {formatEur(alt.part.price_eur)}
                      </span>
                      <button
                        type="button"
                        className={`button button-sm${alt.feasible ? ' button-primary' : ''}`}
                        disabled={busy}
                        onClick={() => onChoose(role, alt)}
                        data-testid="parts-alternative-choose"
                      >
                        {alt.feasible ? 'Choose' : 'Choose anyway'}
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ) : null}
    </Modal>
  );
}
