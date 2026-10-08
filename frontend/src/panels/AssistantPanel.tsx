/** Collapsed placeholder for the Phase 3 Claude assistant. */
export function AssistantPanel() {
  return (
    <details className="card collapsed-card" data-testid="assistant-panel">
      <summary>
        <span>Assistant</span>
        <span className="pill pill-info">Phase 3</span>
      </summary>
      <div className="collapsed-body">
        <p>Assistant arrives in Phase 3.</p>
        <p>
          It will read the current design, the latest analysis and flight data, explain the numbers in
          plain language and suggest changes. It never produces numbers itself; the engine does.
        </p>
      </div>
    </details>
  );
}
