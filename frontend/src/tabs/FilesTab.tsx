/** Placeholder for the Phase 5 export files. */
import { EmptyState } from '../components/EmptyState';

export function FilesTab() {
  return (
    <EmptyState
      badge="Phase 5"
      title="Files arrive in Phase 5"
      description="3D print files (STL and 3MF) split to fit the printer envelope, STEP files of the assembly and each part, dimensioned PDF and DXF drawings, and a bill of materials will be generated here."
      testId="files-placeholder"
    />
  );
}
