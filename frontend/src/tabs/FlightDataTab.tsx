/** Placeholder for the Phase 6 flight-data comparison. */
import { EmptyState } from '../components/EmptyState';

export function FlightDataTab() {
  return (
    <EmptyState
      badge="Phase 6"
      title="Flight data arrives in Phase 6"
      description="Upload ArduPilot DataFlash logs, enter the real built weights, and compare measured hover, transition and cruise figures against the predictions to calibrate the model."
      testId="flight-placeholder"
    />
  );
}
