/**
 * One read log: the phase timeline, per-phase numbers, the charts and the predicted-versus-
 * measured table, each number with a plain explanation.
 */
import type { Comparison, FlightLogDetail, FlightSeries, PhaseStats } from '../../api/flightData';
import { Explain } from '../../components/Explain';
import { StatusPill } from '../../components/StatusPill';
import {
  PHASE_FAMILY_LABEL,
  comparisonMeaning,
  formatClock,
  formatMeasure,
  formatSignedPct,
  rowStatusLabel,
  rowTone,
  shortPhaseLabel,
  splitRows,
  timelineSegments,
  type PhaseFamily,
} from '../../lib/flightData';
import { FlightCharts } from './FlightCharts';

export function PhaseTimeline({ log }: { log: FlightLogDetail }) {
  const phases = log.summary?.phases ?? [];
  const duration = log.summary?.log_duration_s ?? log.result?.duration_s ?? 0;
  if (phases.length === 0) {
    return (
      <p className="small muted" data-testid="flight-timeline-empty">
        No flight phases were found in this log (the aircraft may not have taken off, or the log lacks the messages the
        phase split needs).
      </p>
    );
  }
  const segs = timelineSegments(phases, duration);
  const families = Array.from(new Set(segs.map((s) => s.family))) as PhaseFamily[];
  return (
    <div className="flight-timeline-wrap">
      <div className="flight-timeline" data-testid="flight-timeline" role="list" aria-label="Flight phases along the log">
        {segs.map((s, i) => (
          <div
            key={`${s.phase.key}-${i}`}
            role="listitem"
            className={`flight-phase band-${s.family}`}
            data-testid="flight-phase"
            data-phase={s.phase.key}
            style={{ left: `${s.leftPct}%`, width: `${s.widthPct}%` }}
            title={`${s.phase.label}: ${formatClock(s.phase.start_s)}–${formatClock(s.phase.end_s)} (${formatClock(s.phase.duration_s)})`}
          >
            <span className="flight-phase-label">{s.widthPct > 9 ? shortPhaseLabel(s.phase.key, s.phase.label) : ''}</span>
          </div>
        ))}
      </div>
      <div className="flight-timeline-scale small faint" aria-hidden="true">
        <span>0:00</span>
        <span>{formatClock(duration)}</span>
      </div>
      <ul className="flight-legend small" aria-label="Phase colours">
        {families.map((f) => (
          <li key={f}>
            <span className={`flight-legend-box band-${f}`} aria-hidden="true" />
            {PHASE_FAMILY_LABEL[f]}
          </li>
        ))}
      </ul>
    </div>
  );
}

function stat(v: number | null | undefined, unit: string): string {
  return formatMeasure(v ?? null, unit);
}

function PhaseTable({ phases, decidedBy }: { phases: PhaseStats[]; decidedBy: string | undefined }) {
  return (
    <div className="table-scroll">
      <table className="table table-compact flight-phase-table" data-testid="flight-phase-table">
        <caption className="small muted">
          Per-phase numbers from the log.
          {decidedBy ? <Explain label="how phases are found" text={decidedBy.replace(/[`*]/g, '')} /> : null}
        </caption>
        <thead>
          <tr>
            <th>Phase</th>
            <th className="num">Time</th>
            <th className="num">
              Power <Explain label="phase power" text="Average battery power (volts × amps) during the phase; steady means the part where the aircraft was neither climbing nor descending much." />
            </th>
            <th className="num">
              Energy <Explain label="phase energy" text="Energy taken from the battery during the phase, from the full-rate volts × amps." />
            </th>
            <th className="num">
              Airspeed <Explain label="phase airspeed" text="Median airspeed during the phase (from the airspeed sensor when fitted, else the autopilot's estimate)." />
            </th>
            <th className="num">
              Sag <Explain label="voltage sag" text="How far the pack voltage dropped under load compared with its resting voltage. Large sag means a tired or undersized pack." />
            </th>
            <th>Vibration</th>
          </tr>
        </thead>
        <tbody>
          {phases.map((p, i) => (
            <tr key={`${p.key}-${i}`}>
              <td>{p.label}</td>
              <td className="num">{formatClock(p.duration_s)}</td>
              <td className="num">{stat(p.power_w?.steady_mean ?? p.power_w?.mean, 'W')}</td>
              <td className="num">{stat(p.energy_wh, 'Wh')}</td>
              <td className="num">{stat(p.airspeed_mps?.median, 'm/s')}</td>
              <td className="num">{stat(p.voltage_v?.sag_mean, 'V')}</td>
              <td>{p.vibration?.level ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ComparisonTable({ comparison }: { comparison: Comparison | null }) {
  if (!comparison) return null;
  if (!comparison.available) {
    return (
      <div className="banner banner-warn" data-testid="flight-comparison-unavailable">
        <div className="small">{comparison.reason}</div>
      </div>
    );
  }
  const rows = comparison.comparisons ?? [];
  const { main, energy } = splitRows(rows);
  const renderRow = (r: (typeof rows)[number]) => (
    <tr key={r.key} data-testid="flight-comparison-row" data-key={r.key} data-status={r.status} className={`flight-row-${rowTone(r)}`}>
      <th scope="row">
        {r.label}
        <Explain label={r.label} text={`${comparisonMeaning(r)}\n\nHow it was worked out: ${r.basis}`} />
      </th>
      <td className="num" data-label="Predicted">
        {r.predicted ? (
          <>
            {formatMeasure(r.predicted.value, r.unit)}
            <span className="flight-range faint">
              {r.kind === 'lower_bound' ? 'from ' : ''}
              {formatMeasure(r.predicted.low, '')}–{formatMeasure(r.predicted.high, r.unit)}
            </span>
          </>
        ) : (
          '—'
        )}
      </td>
      <td className="num" data-label="Measured">{formatMeasure(r.measured, r.unit)}</td>
      <td className="num" data-label="Error">{formatSignedPct(r.error_pct)}</td>
      <td className="flight-verdict">
        <StatusPill tone={rowTone(r) === 'ok' ? 'ok' : rowTone(r) === 'warn' ? 'warn' : 'neutral'}>{rowStatusLabel(r)}</StatusPill>
        {r.status === 'outside' ? <p className="small flight-why">{r.explanation}</p> : null}
      </td>
    </tr>
  );
  const head = (
    <thead>
      <tr>
        <th>Quantity</th>
        <th className="num">
          Predicted <Explain label="predicted" text="What the design's analysis predicts at the conditions of this flight (the measured airspeed and the take-off mass), with the range its own uncertainty allows." />
        </th>
        <th className="num">Measured</th>
        <th className="num">
          Error <Explain label="error" text="(measured − predicted) ÷ predicted. Negative means the aircraft did better than predicted for powers and energies." />
        </th>
        <th>Verdict</th>
      </tr>
    </thead>
  );
  return (
    <div data-testid="flight-comparison">
      <p className="small">
        Compared with: <strong>{comparison.reference?.label}</strong>. Mass used:{' '}
        <strong>{formatMeasure(comparison.mass_kg ?? null, 'kg')}</strong>{' '}
        <span className="muted">({comparison.mass_source})</span>
        {comparison.counts ? (
          <>
            {' '}
            · <span data-testid="flight-comparison-counts">{comparison.counts.inside} inside, {comparison.counts.outside} outside the predicted range</span>
          </>
        ) : null}
      </p>
      <div className="table-scroll">
        <table className="table table-compact flight-comparison-table">
          {head}
          <tbody>{main.map(renderRow)}</tbody>
        </table>
      </div>
      {energy.length ? (
        <details className="flight-energy">
          <summary className="small">Energy per phase ({energy.length})</summary>
          <div className="table-scroll">
            <table className="table table-compact flight-comparison-table">
              {head}
              <tbody>{energy.map(renderRow)}</tbody>
            </table>
          </div>
        </details>
      ) : null}
      {comparison.notes?.length ? (
        <ul className="small muted flight-notes">
          {comparison.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function FlightResult({ log, series, seriesError }: { log: FlightLogDetail; series: FlightSeries | null; seriesError: string | null }) {
  const result = log.result;
  return (
    <section className="card" data-testid="flight-result" aria-labelledby="flight-result-heading">
      <div className="card-header">
        <h2 id="flight-result-heading">
          {log.filename}
          {log.sample ? <span className="small muted"> (sample flight)</span> : null}
        </h2>
      </div>
      {log.sample_note ? <p className="small card-note">{log.sample_note}</p> : null}
      <dl className="flight-facts small">
        <div>
          <dt>Firmware</dt>
          <dd>{log.firmware ?? '—'}</dd>
        </div>
        <div>
          <dt>Aircraft</dt>
          <dd>{log.vehicle_type ?? '—'}</dd>
        </div>
        <div>
          <dt>Flight time</dt>
          <dd>{formatClock(log.flight_duration_s)}</dd>
        </div>
        <div>
          <dt>Energy used</dt>
          <dd>{formatMeasure(log.summary?.energy_wh ?? null, 'Wh')}</dd>
        </div>
      </dl>

      <h3 className="flight-subheading">
        Flight phases <Explain label="flight phases" text="The log is split into hover, transition and wing-flight phases from the flight modes, the autopilot's transition messages, the lift-motor outputs and the airspeed." />
      </h3>
      <PhaseTimeline log={log} />
      {result?.phases.length ? <PhaseTable phases={result.phases} decidedBy={result.phase_detection?.method} /> : null}

      <h3 className="flight-subheading">Predicted against measured</h3>
      <ComparisonTable comparison={log.comparison} />

      <h3 className="flight-subheading">Charts</h3>
      {series ? (
        <FlightCharts series={series} vibeWarn={result?.vibration?.thresholds_mps2?.warn ?? null} />
      ) : seriesError ? (
        <p className="small field-error">{seriesError}</p>
      ) : (
        <p className="small muted">Loading charts…</p>
      )}

      {result && (result.missing.length || result.notes.length) ? (
        <details className="flight-missing">
          <summary className="small">What this log could not show ({result.missing.length + result.notes.length})</summary>
          <ul className="small">
            {result.missing.map((m) => (
              <li key={m.type}>
                <code>{m.type}</code> not logged: no {m.effect}.
              </li>
            ))}
            {result.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
