/** "Set up logging" card: the ArduPilot parameters to set, a reason for each, and how to get the log off the aircraft. */
import type { LoggingGuide } from '../../api/flightData';
import { Explain } from '../../components/Explain';

export function LoggingGuideCard({ guide, error }: { guide: LoggingGuide | null; error: string | null }) {
  return (
    <section className="card" data-testid="flight-guide" aria-labelledby="flight-guide-heading">
      <details className="flight-guide">
        <summary>
          <h2 id="flight-guide-heading" className="flight-guide-title">
            Set up logging on the autopilot
          </h2>
          <span className="small muted">
            What to set in ArduPilot before the flight so the log can be compared with the design.
          </span>
        </summary>
        {error ? <p className="small field-error">{error}</p> : null}
        {guide ? (
          <>
            <p className="small">
              Set these parameters once (Mission Planner: Config, Full Parameter List; QGroundControl: Vehicle Setup,
              Parameters), write them, and reboot the autopilot. Nothing extra runs on the drone: ArduPilot writes the log
              to its SD card by itself.
            </p>
            <div className="table-scroll">
              <table className="table table-compact flight-guide-table">
                <thead>
                  <tr>
                    <th>Parameter</th>
                    <th>Set to</th>
                    <th>Why</th>
                  </tr>
                </thead>
                <tbody>
                  {guide.parameters.map((p) => (
                    <tr key={p.param}>
                      <td>
                        <code>{p.param}</code>
                      </td>
                      <td data-testid={p.param === 'LOG_BITMASK' ? 'flight-logbitmask' : undefined}>
                        <strong>{String(p.value)}</strong>
                        {p.param === 'LOG_BITMASK' ? (
                          <Explain label="LOG_BITMASK value" text={`${guide.log_bitmask.explain}\n\nComputed as ${guide.log_bitmask.computation}.`} />
                        ) : null}
                      </td>
                      <td className="small">{p.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <details className="flight-guide-bits">
              <summary className="small">What the {guide.log_bitmask.value} bitmask turns on</summary>
              <ul className="small">
                {guide.log_bitmask.bits.map((b) => (
                  <li key={b.bit}>
                    <strong>{b.name}</strong> (bit {b.bit}, adds {b.value}): {b.logs}
                  </li>
                ))}
              </ul>
              <p className="small muted">Always logged regardless of the bitmask: {guide.always_logged.join('; ')}.</p>
            </details>
            <h3 className="flight-subheading">Getting the log after the flight</h3>
            <ol className="small flight-steps">
              {guide.download.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
            <p className="small faint">Source: {guide.source}</p>
          </>
        ) : !error ? (
          <p className="small muted">Loading…</p>
        ) : null}
      </details>
    </section>
  );
}
