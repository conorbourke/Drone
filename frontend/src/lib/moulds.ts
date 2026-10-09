/**
 * Pure helpers for the Full scale tab (Phase 7; unit-tested): mould file groups with "opens
 * with" notes, tile ids, the draft summary in plain words, draft-map colouring, mould-set status
 * sentences and the motor-out wording.
 */
import type { MotorOutCase } from '../api/fullscale';
import type { DraftMap, MouldDraft, MouldFile, MouldItem, MouldPart } from '../api/moulds';
import { formatNumber, pluralize } from './format';
import { fileName } from './files';

export interface MouldFileGroup {
  key: string;
  title: string;
  description: string;
  opensWith: string;
  files: MouldFile[];
  sizeBytes: number;
}

/** Display order, titles and "opens with" notes of the mould files, by file type. */
export const MOULD_FILE_GROUPS: ReadonlyArray<Omit<MouldFileGroup, 'files' | 'sizeBytes'>> = [
  {
    key: 'stl',
    title: 'Tile print files (STL)',
    description: 'One STL per mould tile, already turned to its print orientation and standing on the bed.',
    opensWith: 'Opens in Bambu Studio, PrusaSlicer or Cura. Print in PETG or ASA with the settings in the mould sheet.',
  },
  {
    key: '3mf',
    title: 'Tile print projects (3MF)',
    description: 'The same tiles as 3MF print projects (millimetres, named), one per tile.',
    opensWith: 'Opens in Bambu Studio, PrusaSlicer or Cura; keeps the tile name and units.',
  },
  {
    key: 'step',
    title: 'Mould halves (STEP CAD)',
    description: 'Each mould half with all its tiles in aircraft position, for checking or editing in CAD.',
    opensWith: 'Opens in FreeCAD, Fusion or an online STEP viewer.',
  },
  {
    key: 'pdf',
    title: 'Mould sheets (PDF)',
    description: 'One sheet per part: the parting line, the draft report, the tile layout and the assembly order. Print it for the workshop.',
    opensWith: 'Opens in any PDF reader.',
  },
];

const OTHER_GROUP = {
  key: 'other',
  title: 'Other files',
  description: 'Further files of this mould set.',
  opensWith: 'Download and open it with a program for this file type.',
};

/** Files grouped by type in a fixed order (unknown types last); empty groups dropped. */
export function groupMouldFiles(files: MouldFile[]): MouldFileGroup[] {
  const known = new Set(MOULD_FILE_GROUPS.map((g) => g.key));
  const buckets = new Map<string, MouldFile[]>();
  for (const f of files) {
    const key = known.has(f.kind.toLowerCase()) ? f.kind.toLowerCase() : 'other';
    const list = buckets.get(key) ?? [];
    list.push(f);
    buckets.set(key, list);
  }
  return [...MOULD_FILE_GROUPS, OTHER_GROUP]
    .filter((g) => buckets.has(g.key))
    .map((g) => {
      const list = buckets.get(g.key)!;
      return { ...g, files: list, sizeBytes: list.reduce((s, f) => s + (f.size_bytes || 0), 0) };
    });
}

/** The tile id the mesh endpoint takes: the STL file stem. */
export function tileId(stlPath: string): string {
  return fileName(stlPath).replace(/\.[^.]+$/, '');
}

/** "12 %" from a 0-1 fraction. */
export function percentText(fraction: number | null | undefined, digits = 0): string {
  if (fraction === null || fraction === undefined || !Number.isFinite(fraction)) return '—';
  return `${formatNumber(fraction * 100, { maxFractionDigits: digits })}\u202f%`;
}

/** "2.1°" (a negative zero reads as 0°). */
export function degText(deg: number | null | undefined, digits = 1): string {
  if (deg === null || deg === undefined || !Number.isFinite(deg)) return '—';
  const v = Math.abs(deg) < 0.05 ? 0 : deg;
  return `${formatNumber(v, { maxFractionDigits: digits })}°`;
}

/** Plain sentence on the draft check of one part. */
export function draftSummaryText(draft: MouldDraft): string {
  const min = degText(draft.min_draft_deg);
  const flagged = draft.flagged_faces.length;
  const outside = draft.summary.fraction_below_min_outside_band ?? 0;
  if (flagged === 0) {
    return `Every face has at least ${min} of draft away from the parting line, so the part should come out of both halves cleanly.`;
  }
  return (
    `${pluralize(flagged, 'face has', 'faces have')} less than ${min} of draft away from the parting line ` +
    `(${percentText(outside)} of the surface). These are walls that run almost straight along the pull direction, ` +
    'so the laminate may grip them when the half is lifted off.'
  );
}

/** What to do about flagged faces, in plain words. */
export const FLAGGED_ADVICE =
  'The part surface was not changed. Wax and release-film those faces well, flex the laminate out gently when demoulding, or give the cross-section a little taper in the design if a part sticks.';

/** Explanation of one flagged face: where it is and how much area is affected. */
export function flaggedFaceText(f: { where: string; min_draft_deg: number; area_below_min_mm2: number }, minDraft: number): string {
  const cm2 = f.area_below_min_mm2 / 100;
  return `${f.where}: as little as ${degText(f.min_draft_deg)} of draft; ${formatNumber(cm2, { maxFractionDigits: 0 })}\u202fcm² is below ${degText(minDraft)}.`;
}

export type DraftBin = 'band' | 'flag' | 'low' | 'ok';

/** Colour class of one draft sample: in the parting band, below the minimum, within twice it, or fine. */
export function draftBin(deg: number, minDraft: number, inBand: boolean): DraftBin {
  if (inBand) return 'band';
  if (deg < minDraft) return 'flag';
  if (deg < 2 * minDraft) return 'low';
  return 'ok';
}

export interface DraftDot {
  x: number;
  y: number;
  bin: DraftBin;
}

/**
 * The draft map of one half as dots scaled into a `width` × `height` box (u along the part,
 * v across it), keeping the aspect ratio. Long axis left to right.
 */
export function draftDots(map: DraftMap, half: string, minDraft: number, width: number, height: number): DraftDot[] {
  const idx: number[] = [];
  for (let i = 0; i < map.half.length; i += 1) if (map.half[i] === half) idx.push(i);
  if (idx.length === 0) return [];
  const us = idx.map((i) => map.points_uv_mm[i][0]);
  const vs = idx.map((i) => map.points_uv_mm[i][1]);
  const u0 = Math.min(...us);
  const u1 = Math.max(...us);
  const v0 = Math.min(...vs);
  const v1 = Math.max(...vs);
  const pad = 4;
  const scale = Math.min((width - 2 * pad) / Math.max(1e-6, u1 - u0), (height - 2 * pad) / Math.max(1e-6, v1 - v0));
  const ox = pad + (width - 2 * pad - (u1 - u0) * scale) / 2;
  const oy = pad + (height - 2 * pad - (v1 - v0) * scale) / 2;
  return idx.map((i) => ({
    x: ox + (map.points_uv_mm[i][0] - u0) * scale,
    y: height - (oy + (map.points_uv_mm[i][1] - v0) * scale),
    bin: draftBin(map.draft_deg[i], minDraft, map.in_band[i]),
  }));
}

/** Tiles of every half of a part. */
export function partTiles(part: MouldPart): number {
  return part.halves.reduce((s, h) => s + h.tiles.length, 0);
}

/** Total estimated filament of a part's tiles, grams. */
export function partMassG(part: MouldPart): number {
  return part.halves.reduce((s, h) => s + h.tiles.reduce((t, x) => t + (x.estimated_mass_g ?? 0), 0), 0);
}

const PART_NAMES: Record<string, string> = {
  nose: 'nose',
  fuselage: 'fuselage',
  wing_root_fairing: 'wing-root fairing',
};

/** "nose, fuselage and wing-root fairing" */
export function mouldPartsText(parts: readonly string[]): string {
  const names = parts.map((p) => PART_NAMES[p] ?? p);
  if (names.length <= 1) return names.join('');
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

/** Plain status of a mould set for the list of earlier sets. */
export function mouldStatusText(item: Pick<MouldItem, 'status' | 'progress' | 'queue_position' | 'summary'>): string {
  switch (item.status) {
    case 'queued':
      return item.queue_position !== null && item.queue_position > 0
        ? `Waiting (${item.queue_position} ahead in the queue)`
        : 'Waiting to start';
    case 'running':
      return `Making the moulds (${Math.round(Math.max(0, Math.min(1, item.progress)) * 100)} %)`;
    case 'done':
      return item.summary ? `Ready: ${pluralize(item.summary.tiles, 'tile')}` : 'Ready';
    default:
      return 'Failed';
  }
}

/** Short verdict of one motor-out case. */
export function motorOutVerdict(c: Pick<MotorOutCase, 'holds_attitude_and_heading' | 'holds_attitude_spinning'>): string {
  if (c.holds_attitude_and_heading) return 'Holds attitude and heading';
  if (c.holds_attitude_spinning) return 'Stays level but spins';
  return 'Cannot stay level';
}

/** "82 %" busiest motor, or a dash when no balance exists. */
export function utilisationText(u: number | null | undefined): string {
  return u === null || u === undefined ? '—' : percentText(u);
}

/** Ply direction with degrees: "+-45" -> "±45°", "0 (spanwise)" -> "0° (spanwise)". */
export function plyOrientationText(o: string): string {
  return o.replace(/\+-/g, '±').replace(/^([±+\-0-9/]+)/, '$1°');
}
