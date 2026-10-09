/**
 * The parts catalogue (the Phase 1 browser, below the Phase 4 parts list): every part in the
 * database by category, with search, its specification summary, listings and verification state.
 */
import { Fragment, useEffect, useState } from 'react';
import { api, errorMessage, isAbortError, isAuthError } from '../../api/client';
import type { Part, PartCategory } from '../../api/types';
import { EmptyState } from '../../components/EmptyState';
import { Explain } from '../../components/Explain';
import { StatusPill } from '../../components/StatusPill';
import { formatEur, formatMassG, formatWithUnit, humanizeKey, unitForKey } from '../../lib/format';

const SEARCH_DEBOUNCE_MS = 300;

interface SpecItem {
  key: string;
  label: string;
  text: string;
  /** Explanation from the category metadata; empty when the server has none. */
  description: string;
}

/** Up to four spec values of a part, each with its label and explanation from the category. */
function specSummary(part: Part, category: PartCategory | undefined): SpecItem[] {
  const entries = Object.entries(part.spec).filter(([, value]) => typeof value === 'number' || typeof value === 'string');
  return entries.slice(0, 4).map(([key, value]) => {
    const field = category?.fields.find((f) => f.name === key);
    const label = field?.label ?? humanizeKey(key);
    const unit = field?.unit ?? unitForKey(key);
    const text = typeof value === 'number' ? formatWithUnit(value, unit) : String(value);
    return { key, label, text, description: field?.description ?? '' };
  });
}

export function PartsCatalogue() {
  const [categories, setCategories] = useState<PartCategory[] | null>(null);
  const [categoriesError, setCategoriesError] = useState<string | null>(null);
  const [category, setCategory] = useState<string>('');
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [parts, setParts] = useState<Part[] | null>(null);
  const [partsError, setPartsError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    api<PartCategory[]>('/api/parts/categories', { signal: controller.signal })
      .then((list) => setCategories(list))
      .catch((error: unknown) => {
        if (isAbortError(error) || isAuthError(error)) return;
        setCategoriesError(errorMessage(error));
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams();
    if (category) params.set('category', category);
    if (debouncedQuery) params.set('q', debouncedQuery);
    const suffix = params.toString();
    api<Part[]>(`/api/parts${suffix ? `?${suffix}` : ''}`, { signal: controller.signal })
      .then((list) => {
        setParts(list);
        setPartsError(null);
      })
      .catch((error: unknown) => {
        if (isAbortError(error) || isAuthError(error)) return;
        setPartsError(errorMessage(error));
      });
    return () => controller.abort();
  }, [category, debouncedQuery]);

  const selectedCategory = categories?.find((c) => c.key === category);

  return (
    <details className="card collapsed-card parts-catalogue" data-testid="parts-catalogue">
      <summary>
        <span id="parts-heading">Parts catalogue</span>
        {parts && !category && !debouncedQuery ? <span className="pill">{parts.length}</span> : null}
      </summary>
      <div className="collapsed-body parts-catalogue-body">
        <p className="card-note">
          Every part in the database, by category, with the specification the engine uses. The list above picks from
          these. Parts marked <strong>Unverified</strong> have numbers taken from the manufacturer page that nobody has
          checked yet: open the source link before buying.
        </p>
        <div className="field-grid">
          <div className="field">
            <div className="field-head">
              <label htmlFor="parts-category" className="field-label">
                Category
              </label>
            </div>
            <div className="field-control">
              <select
                id="parts-category"
                data-testid="parts-category"
                value={category}
                onChange={(event) => setCategory(event.target.value)}
                disabled={!categories}
              >
                <option value="">All categories</option>
                {(categories ?? []).map((c) => (
                  <option key={c.key} value={c.key}>
                    {c.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="field">
            <div className="field-head">
              <label htmlFor="parts-search" className="field-label">
                Search
              </label>
            </div>
            <div className="field-control">
              <input
                id="parts-search"
                data-testid="parts-search"
                type="search"
                placeholder="Manufacturer or model"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>
          </div>
        </div>
        {categoriesError ? (
          <p className="form-error" role="alert">
            Could not load categories: {categoriesError}
          </p>
        ) : null}
        {selectedCategory ? (
          <details className="collapsed-card" style={{ marginTop: 'var(--space-3)' }}>
            <summary>
              Specification fields for {selectedCategory.label}
              <span className="pill">{selectedCategory.fields.length}</span>
            </summary>
            <div className="collapsed-body">
              {selectedCategory.description ? <p>{selectedCategory.description}</p> : null}
              <ul>
                {selectedCategory.fields.map((field) => (
                  <li key={field.name}>
                    <strong>{field.label}</strong>
                    {field.unit ? ` (${field.unit})` : ''}
                    {field.required ? '' : ' · optional'}
                    {field.description ? ` — ${field.description}` : ''}
                  </li>
                ))}
              </ul>
            </div>
          </details>
        ) : null}

        <div className="card-header parts-catalogue-head">
          <h3 id="parts-list-heading">{selectedCategory ? selectedCategory.label : 'All parts'}</h3>
          {parts ? <span className="muted small">{parts.length} shown</span> : null}
        </div>
        {partsError ? (
          <p className="form-error" role="alert">
            Could not load parts: {partsError}
          </p>
        ) : parts === null ? (
          <p className="loading small">Loading parts…</p>
        ) : parts.length === 0 ? (
          <EmptyState
            title="No parts found"
            description={
              debouncedQuery || category
                ? 'Nothing in the database matches this filter.'
                : 'The parts database is empty. Add parts through the API, or restore a backup that has them.'
            }
          />
        ) : (
          <div className="table-scroll">
            <table className="table" data-testid="parts-table">
              <thead>
                <tr>
                  <th scope="col">Part</th>
                  <th scope="col">Category</th>
                  <th scope="col" className="num">
                    <span className="row" style={{ justifyContent: 'flex-end', gap: '4px' }}>
                      Mass
                      <Explain label="mass" text="Mass of the part in grams, as listed by the manufacturer. Feeds the weight build-up and balance estimate." />
                    </span>
                  </th>
                  <th scope="col" className="num">
                    <span className="row" style={{ justifyContent: 'flex-end', gap: '4px' }}>
                      Price
                      <Explain label="price" text="Price estimate in euro from the catalogue. The parts list above uses the best Irish or UK listing when one shows a price." />
                    </span>
                  </th>
                  <th scope="col">Specification</th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {parts.map((part) => {
                  const partCategory = categories?.find((c) => c.key === part.category);
                  return (
                    <tr key={part.id} data-testid="part-row" data-part-id={part.id}>
                      <td>
                        <div className="list-item-title">
                          {part.manufacturer} {part.model}
                        </div>
                        {part.notes ? <div className="list-item-meta">{part.notes}</div> : null}
                        {part.listings.length > 0 ? (
                          <div className="list-item-meta">
                            {part.listings.map((listing) => (
                              <span key={listing.id}>
                                <a href={listing.url} target="_blank" rel="noreferrer noopener">
                                  {listing.supplier_name} ({listing.country})
                                </a>
                                {listing.price_eur !== null ? ` ${formatEur(listing.price_eur)}` : ''}{' '}
                              </span>
                            ))}
                          </div>
                        ) : null}
                      </td>
                      <td>{partCategory?.label ?? humanizeKey(part.category)}</td>
                      <td className="num">{formatMassG(part.mass_g)}</td>
                      <td className="num">{formatEur(part.price_eur_estimate)}</td>
                      <td className="small">
                        {specSummary(part, partCategory).map((item, index) => (
                          <Fragment key={item.key}>
                            {index > 0 ? ' · ' : null}
                            <span className="spec-item" title={item.description || undefined}>
                              {item.label} {item.text}
                              <Explain text={item.description} label={item.label} />
                            </span>
                          </Fragment>
                        ))}
                      </td>
                      <td>
                        {part.verified ? (
                          <StatusPill tone="ok" title={part.source}>
                            Verified
                          </StatusPill>
                        ) : (
                          <StatusPill tone="warn" title={part.source} testId="part-unverified">
                            Unverified
                          </StatusPill>
                        )}
                        {part.source ? <div className="list-item-meta">{part.source}</div> : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </details>
  );
}
