/**
 * Tier 1 aerodynamics and performance engine (pure TypeScript, no React, no DOM).
 * Methods, constants and sources: docs/ENGINE.md. Contract: docs/phases/PHASE2.md section 5.
 */

export { estimate } from './estimate';
export { compareLayouts, LAYOUTS, LAYOUT_LABELS, ARDUPILOT_NOTES } from './compare';
export { buildGeometry, withDefaults, naca4Coordinates, airfoilShape } from './geometry';
export type { BuildGeometryOptions, ResolvedParameters } from './geometry';
export { DESIGN_V2_DEFAULTS } from './constants';
export { PARTS_MASS_LABELS, partMass } from './mass';
export type {
  AeroResult,
  AirfoilSummaryMap,
  BalanceResult,
  BoomGeometry,
  DragItem,
  EngineInput,
  Estimates,
  FuselageGeometry,
  FuselageStation,
  Geometry,
  LayoutComparison,
  MassComponent,
  MassResult,
  MissionSegment,
  PartsMasses,
  PerformanceResult,
  Quantity,
  RenderPrimitives,
  RotorGeometry,
  Status,
  StatusLevel,
  TailGeometry,
  Vec3,
  WingGeometry,
} from './types';
