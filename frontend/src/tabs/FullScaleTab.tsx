/**
 * Full scale tab (Phase 7, docs/phases/PHASE7.md): for the draft or a saved version, the
 * full-scale (24 kg, carbon) checks and the moulds for the curved carbon parts.
 */
import { useId, useState } from 'react';
import type { ExportSource } from '../api/exports';
import { useWorkspace } from '../lib/workspace';
import { FullScaleChecks } from './fullscale/FullScaleChecks';
import { MouldsSection } from './fullscale/MouldsSection';
import '../styles/files.css';
import '../styles/fullscale.css';

export function FullScaleTab() {
  const { projectId, versions, flushDraft } = useWorkspace();
  const selectId = useId();
  const [choice, setChoice] = useState('draft');
  const selected = choice === 'draft' ? null : (versions?.find((v) => String(v.id) === choice) ?? null);
  const source: ExportSource = selected ? { version_id: selected.id } : 'draft';
  const sourceText = selected ? `v${selected.number} “${selected.name}”` : 'the draft';

  return (
    <div className="stack fullscale-tab" data-testid="fullscale-tab">
      <section className="card" aria-labelledby="fullscale-source-heading">
        <div className="card-header">
          <h2 id="fullscale-source-heading">Full scale and moulds</h2>
        </div>
        <p className="card-note">
          The final aircraft is built in carbon at up to 24&#8239;kg. Check that design here, then make the moulds its carbon nose, fuselage and
          wing-root fairings are laid up in. Scale the prototype up with “Scale to weight” on the Design tab first, and set the mission scale
          to final.
        </p>
        <div className="analysis-controls">
          <label htmlFor={selectId} className="small muted">
            Design
          </label>
          <select
            id={selectId}
            className="input analysis-source"
            data-testid="fullscale-source"
            value={selected ? String(selected.id) : 'draft'}
            onChange={(e) => setChoice(e.target.value)}
          >
            <option value="draft">The draft</option>
            {(versions ?? []).map((v) => (
              <option key={v.id} value={String(v.id)}>
                v{v.number} {v.name}
              </option>
            ))}
          </select>
        </div>
      </section>
      <FullScaleChecks key={`checks-${choice}`} projectId={projectId} source={source} sourceText={sourceText} flushDraft={flushDraft} />
      <MouldsSection source={source} sourceText={sourceText} />
    </div>
  );
}
