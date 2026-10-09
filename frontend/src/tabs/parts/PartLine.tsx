/**
 * One line of the parts list: role, part, quantity, unit and line mass and price, the best
 * supplier link with its country, last-checked date and link status, flags as chips with plain
 * messages, the reasoning on demand, lock state and the Replace / Unlock / Check prices now actions.
 */
import { useId, useState } from 'react';
import type { PartsListRole } from '../../api/partsList';
import type { ListingsRefreshStatus } from '../../api/types';
import { Explain } from '../../components/Explain';
import { StatusPill } from '../../components/StatusPill';
import { formatDate, formatDateTime, formatEur, formatMassG } from '../../lib/format';
import { flagInfo, linkState, quantityText, refreshActive, refreshLabel, relativeTime } from '../../lib/partsList';
import { CountryFlag } from './CountryFlag';

export interface RefreshState {
  status: ListingsRefreshStatus | null;
  message: string | null;
  at: string | null;
  /** Error from the last "Check prices now" request (429, 503 ...). */
  error: string | null;
}

interface PartLineProps {
  role: PartsListRole;
  refresh: RefreshState | undefined;
  busy: boolean;
  onReplace: (role: PartsListRole) => void;
  onUnlock: (role: PartsListRole) => void;
  onRefresh: (partId: number) => void;
}

const PRICE_SOURCE: Record<string, string> = {
  listing: 'best listing',
  estimate: 'catalogue estimate',
};

export function PartLine({ role, refresh, busy, onReplace, onUnlock, onRefresh }: PartLineProps) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const part = role.part;

  if (!role.filled || !part) {
    return (
      <article className="parts-line parts-line-unfilled" data-testid="parts-unfilled" data-role={role.role}>
        <div className="parts-line-head">
          <div className="parts-line-role">{role.label}</div>
          <StatusPill tone="warn">Not filled</StatusPill>
        </div>
        <p className="small">{role.unfilled_reason ?? role.reasoning[0] ?? 'No catalogue part fits this role.'}</p>
        {role.alternatives.length > 0 ? (
          <div className="parts-line-actions">
            <button type="button" className="button button-sm" onClick={() => onReplace(role)} disabled={busy} data-testid="parts-replace">
              Choose a part
            </button>
          </div>
        ) : null}
      </article>
    );
  }

  const listing = role.best_listing;
  const link = listing ? linkState(listing) : null;
  const unverified = role.flags.find((f) => f.code === 'unverified');
  const otherFlags = role.flags.filter((f) => f.code !== 'unverified');
  const refreshInfo = refreshLabel(refresh?.status);
  const refreshing = refreshActive(refresh?.status);
  const priceSource = role.price_source ? PRICE_SOURCE[role.price_source] ?? role.price_source : null;
  const qty = quantityText(role);

  return (
    <article
      className={`parts-line${role.locked ? ' parts-line-locked' : ''}`}
      data-testid="parts-line"
      data-role={role.role}
      data-part-id={part.id}
      data-locked={role.locked ? 'true' : 'false'}
    >
      <div className="parts-line-head">
        <div className="parts-line-ident">
          <div className="parts-line-role">
            {role.label}
            {role.locked ? (
              <StatusPill tone="info" testId="parts-locked" title="Your choice: kept when the design changes until you unlock it.">
                Your choice
              </StatusPill>
            ) : null}
          </div>
          <div className="parts-line-part" data-testid="parts-line-part">
            {part.manufacturer} {part.model}
          </div>
          {role.custom_pack ? (
            <div className="small muted">
              Custom {role.custom_pack.cells_series}S{role.custom_pack.cells_parallel}P pack of {role.custom_pack.cell}
            </div>
          ) : null}
        </div>
        <dl className="parts-line-figures">
          <div>
            <dt>Qty</dt>
            <dd data-testid="parts-line-qty" data-value={role.quantity}>
              {qty}
            </dd>
          </div>
          <div>
            <dt>Mass</dt>
            <dd data-testid="parts-line-mass" data-value={role.line_mass_g ?? ''}>
              {formatMassG(role.line_mass_g)}
              <span className="parts-line-unit">{formatMassG(role.unit_mass_g)} each</span>
            </dd>
          </div>
          <div>
            <dt>Price</dt>
            <dd data-testid="parts-line-price" data-value={role.line_price_eur ?? ''}>
              {role.line_price_eur !== null ? formatEur(role.line_price_eur) : 'No price'}
              <span className="parts-line-unit">
                {role.unit_price_eur !== null ? `${formatEur(role.unit_price_eur)} each` : 'not counted'}
                {priceSource ? ` · ${priceSource}` : ''}
              </span>
            </dd>
          </div>
        </dl>
      </div>

      <div className="parts-line-supplier">
        {listing ? (
          <>
            <CountryFlag country={listing.country} />
            <a
              href={listing.url}
              target="_blank"
              rel="noreferrer noopener"
              className="parts-supplier-link"
              data-testid="parts-supplier-link"
            >
              {listing.supplier_name}
              <span className="visually-hidden"> (opens the shop page in a new tab)</span>
            </a>
            {listing.price_eur !== null ? <span className="small muted">{formatEur(listing.price_eur)}</span> : null}
            {listing.in_stock === true ? <span className="small muted">in stock</span> : listing.in_stock === false ? <span className="small muted">out of stock</span> : null}
            <span className="small muted" data-testid="parts-last-checked">
              checked{' '}
              <time dateTime={listing.last_checked_at ?? undefined} title={formatDateTime(listing.last_checked_at)}>
                {relativeTime(listing.last_checked_at)} ({formatDate(listing.last_checked_at)})
              </time>
            </span>
            {link ? (
              <span className={`pill pill-${link.tone}`} data-testid="parts-link-status" data-status={link.key} title={link.explain}>
                {link.label}
              </span>
            ) : null}
            {role.listings.length > 1 ? (
              <span className="small faint">+{role.listings.length - 1} more {role.listings.length === 2 ? 'shop' : 'shops'}</span>
            ) : null}
          </>
        ) : (
          <span className="small muted" data-testid="parts-no-listing">
            No Irish or UK shop listing yet.
          </span>
        )}
        {refreshInfo ? (
          <span
            className={`pill pill-${refreshInfo.tone}`}
            data-testid="parts-refresh-status"
            data-status={refresh?.status ?? ''}
            title={refresh?.message ?? undefined}
          >
            {refreshing ? <span className="chip-spinner" aria-hidden="true" /> : null}
            {refreshInfo.label}
          </span>
        ) : null}
      </div>

      <ul className="parts-flags" aria-label="Notes on this part">
        {unverified ? (
          <li className="parts-flag-item" data-testid="parts-flag" data-code="unverified">
            <StatusPill tone="warn">Unverified spec</StatusPill>
            <span className="small muted">
              Check the numbers on the{' '}
              <a href={part.source} target="_blank" rel="noreferrer noopener" data-testid="parts-unverified-source">
                manufacturer page
              </a>{' '}
              before buying.
            </span>
          </li>
        ) : null}
        {otherFlags.map((flag, index) => {
          const info = flagInfo(flag.code);
          return (
            <li key={`${flag.code}-${index}`} className="parts-flag-item" data-testid="parts-flag" data-code={flag.code}>
              <StatusPill tone={info.tone}>{info.label}</StatusPill>
              <span className="small muted">{flag.message}</span>
            </li>
          );
        })}
      </ul>

      {refresh?.error || (refresh?.message && refresh.status && refresh.status !== 'queued') ? (
        <p
          className={`small ${refresh.error || refresh.status === 'error' ? 'form-error' : 'muted'}`}
          role="status"
          data-testid="parts-refresh-message"
        >
          {refresh.error ?? refresh.message}
          {!refresh.error && refresh.at ? ` (${relativeTime(refresh.at)})` : ''}
        </p>
      ) : null}

      <div className="parts-line-actions">
        <button
          type="button"
          className="button button-ghost button-sm"
          aria-expanded={open}
          aria-controls={detailsId}
          onClick={() => setOpen((v) => !v)}
          data-testid="parts-reasoning-toggle"
        >
          {open ? 'Hide why' : 'Why this part'}
        </button>
        <button type="button" className="button button-sm" onClick={() => onReplace(role)} disabled={busy} data-testid="parts-replace">
          Replace
        </button>
        {role.locked ? (
          <button type="button" className="button button-sm" onClick={() => onUnlock(role)} disabled={busy} data-testid="parts-unlock">
            Unlock
          </button>
        ) : null}
        <button
          type="button"
          className="button button-ghost button-sm"
          onClick={() => onRefresh(part.id)}
          disabled={refreshing}
          data-testid="parts-refresh"
        >
          Check prices now
        </button>
        <Explain
          label="Check prices now"
          text="Claude searches Irish and UK shops for this exact part and the server checks every link it finds. It takes up to a couple of minutes, and each part can be checked once an hour."
        />
      </div>

      {open ? (
        <div className="parts-reasoning" id={detailsId} data-testid="parts-reasoning">
          <ul className="small">
            {role.reasoning.map((sentence, index) => (
              <li key={index}>{sentence}</li>
            ))}
          </ul>
          {role.listings.length > 1 ? (
            <div className="small">
              <strong>All listings:</strong>
              <ul>
                {role.listings.map((li) => (
                  <li key={li.id}>
                    <a href={li.url} target="_blank" rel="noreferrer noopener">
                      {li.supplier_name}
                    </a>{' '}
                    ({li.country}) {li.price_eur !== null ? formatEur(li.price_eur) : 'no price'} · {linkState(li).label.toLowerCase()} ·
                    checked {formatDate(li.last_checked_at)}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : (
        <div id={detailsId} hidden />
      )}
    </article>
  );
}
