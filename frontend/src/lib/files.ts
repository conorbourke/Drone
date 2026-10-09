/**
 * Pure helpers for the Files tab (Phase 5; unit-tested): file groups in a fixed order with
 * plain-language labels, "opens with" explanations per file type, piece ids, envelope and size
 * texts, export status sentences and the preview mesh decoding.
 */
import type { ExportFile, ExportItem, ExportManifest, ExportPart } from '../api/exports';
import { formatNumber, pluralize } from './format';

export interface FileGroupInfo {
  key: string;
  title: string;
  /** One sentence on what the group holds. */
  description: string;
}

/** Display order and wording of the manifest's file groups. */
export const FILE_GROUPS: FileGroupInfo[] = [
  {
    key: 'print',
    title: '3D print files',
    description:
      'One STL per printed piece (already turned to its print orientation) and a 3MF print project per part, plus one 3MF with every piece arranged on printer plates.',
  },
  {
    key: 'cad',
    title: 'CAD models',
    description: 'STEP models of each part and of the whole assembly (with tubes, motors, propellers, battery and payload), in millimetres.',
  },
  {
    key: 'drawings',
    title: 'Drawings',
    description:
      'Dimensioned PDF drawings (general arrangement, wing planform, tail, stations, centre of gravity) and DXF outlines of flat parts that can be CNC-cut from carbon plate.',
  },
  {
    key: 'bom',
    title: 'Bill of materials',
    description: 'Every part, tube, fastener and printed piece with quantities, masses, prices in euro and suppliers, as a spreadsheet.',
  },
  {
    key: 'notes',
    title: 'Printing notes',
    description: 'Per part: filament, nozzle, layer height, walls, infill, orientation, supports, temperatures and how to bond the joints.',
  },
];

const OTHER_GROUP: FileGroupInfo = {
  key: 'other',
  title: 'Other files',
  description: 'Further files of this export.',
};

/** Fallback "opens with" text per file type, used when the manifest gives none. */
export const OPENS_WITH: Record<string, string> = {
  stl: 'STL mesh of one printed piece, already in its print orientation. Opens in Bambu Studio, PrusaSlicer or Cura.',
  '3mf': '3MF print project (millimetres, named and coloured pieces). Opens in Bambu Studio, PrusaSlicer or Cura.',
  step: 'STEP CAD model. Opens in FreeCAD or an online STEP viewer.',
  stp: 'STEP CAD model. Opens in FreeCAD or an online STEP viewer.',
  pdf: 'PDF document. Opens in any PDF reader.',
  dxf: 'DXF flat-part outline for CNC cutting. Opens in LibreCAD, QCAD or FreeCAD.',
  csv: 'Spreadsheet (CSV). Opens in LibreOffice Calc, Excel or Google Sheets.',
  md: 'Plain-text notes (Markdown). Opens in any text editor.',
  json: 'Machine-readable description of this export.',
};

/** Short label of a file type: "STL", "3MF", "STEP" ... */
export function kindLabel(kind: string): string {
  return kind === 'md' ? 'Notes' : kind.toUpperCase();
}

/** The plain "what is it and which free program opens it" text of a file. */
export function opensWith(file: Pick<ExportFile, 'kind' | 'opens_with'>): string {
  const given = file.opens_with?.trim();
  if (given) return given;
  return OPENS_WITH[file.kind.toLowerCase()] ?? 'Download and open it with a program for this file type.';
}

export interface FileGroup extends FileGroupInfo {
  files: ExportFile[];
  sizeBytes: number;
}

/** Files grouped in the fixed order (unknown groups last, under "Other files"); empty groups dropped. */
export function groupFiles(files: ExportFile[]): FileGroup[] {
  const known = new Map(FILE_GROUPS.map((g) => [g.key, g]));
  const buckets = new Map<string, ExportFile[]>();
  for (const f of files) {
    const key = known.has(f.group) ? f.group : 'other';
    const list = buckets.get(key) ?? [];
    list.push(f);
    buckets.set(key, list);
  }
  return [...FILE_GROUPS, OTHER_GROUP]
    .filter((g) => buckets.has(g.key))
    .map((g) => {
      const list = buckets.get(g.key)!;
      return { ...g, files: list, sizeBytes: list.reduce((s, f) => s + (f.size_bytes || 0), 0) };
    });
}

/** The file name of a path inside the export. */
export function fileName(path: string): string {
  return path.split('/').pop() ?? path;
}

/** The piece id the mesh endpoint takes: the STL file stem. */
export function pieceId(stlPath: string): string {
  return fileName(stlPath).replace(/\.[^.]+$/, '');
}

/** "240 × 240 × 240 mm" (or "256 × 256 mm" for a bed). */
export function dimsText(mm: readonly number[] | null | undefined, digits = 0): string {
  if (!mm || mm.length === 0 || mm.some((v) => !Number.isFinite(v))) return '—';
  return `${mm.map((v) => formatNumber(v, { maxFractionDigits: digits })).join(' × ')}\u202fmm`;
}

/** Total printed pieces of a part, counting every copy (for example two wing tips). */
export function piecesToPrint(part: ExportPart): number {
  return part.pieces.length * Math.max(1, part.quantity || 1);
}

export function allPiecesFit(manifest: ExportManifest | null | undefined): boolean | null {
  if (!manifest?.parts) return null;
  const pieces = manifest.parts.flatMap((p) => p.pieces);
  if (pieces.length === 0) return null;
  return pieces.every((pc) => pc.fits);
}

/** Text of a manifest warning (strings, or objects with a message). */
export function warningText(w: unknown): string {
  if (typeof w === 'string') return w;
  if (w && typeof w === 'object') {
    const o = w as Record<string, unknown>;
    for (const k of ['message', 'text', 'detail']) if (typeof o[k] === 'string') return o[k] as string;
  }
  return String(w);
}

/** What was used to make the files: "the draft" or "v3". */
export function exportSourceText(item: Pick<ExportItem, 'source' | 'version_number'>): string {
  return item.source === 'version' ? `v${item.version_number ?? '?'}` : 'the draft';
}

/** Plain status of an export for the list of past exports. */
export function exportStatusText(item: Pick<ExportItem, 'status' | 'progress' | 'queue_position' | 'file_count'>): string {
  switch (item.status) {
    case 'queued':
      return item.queue_position !== null && item.queue_position > 0
        ? `Waiting (${item.queue_position} ahead in the queue)`
        : 'Waiting to start';
    case 'running':
      return `Making the files (${Math.round(Math.max(0, Math.min(1, item.progress)) * 100)} %)`;
    case 'done':
      return item.file_count !== null ? `Ready: ${pluralize(item.file_count, 'file')}` : 'Ready';
    default:
      return 'Failed';
  }
}

/** Decode a base64 little-endian buffer (the preview mesh encoding). */
export function decodeBase64(data: string): ArrayBuffer {
  const binary = atob(data);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

/** The preview mesh as typed arrays (float32 positions in mm, uint32 triangle indices). */
export function decodeMesh(mesh: { positions: string; indices: string }): { positions: Float32Array; indices: Uint32Array } {
  // Copy into aligned buffers (little-endian hosts read them directly; every browser is one).
  const positions = new Float32Array(decodeBase64(mesh.positions));
  const indices = new Uint32Array(decodeBase64(mesh.indices));
  return { positions, indices };
}

/**
 * Where a piece of size `size` sits on a bed of `bed` (both mm): centred on the bed, resting
 * on it. Returns the offset to add to the mesh coordinates (whose minimum is `min`).
 */
export function bedOffset(min: readonly number[], max: readonly number[], bed: readonly number[]): [number, number, number] {
  const cx = (min[0] + max[0]) / 2;
  const cy = (min[1] + max[1]) / 2;
  return [bed[0] / 2 - cx, bed[1] / 2 - cy, -min[2]];
}
