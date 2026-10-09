import { describe, expect, it } from 'vitest';
import type { ExportFile, ExportPart } from '../api/exports';
import { fileUrl } from '../api/exports';
import {
  allPiecesFit,
  bedOffset,
  decodeMesh,
  dimsText,
  exportSourceText,
  exportStatusText,
  groupFiles,
  opensWith,
  pieceId,
  piecesToPrint,
  warningText,
} from './files';

const file = (path: string, group: string, kind: string, size = 10, opens = ''): ExportFile => ({
  path,
  group,
  kind,
  size_bytes: size,
  opens_with: opens,
});

describe('groupFiles', () => {
  it('keeps the fixed group order, sums sizes and puts unknown groups last', () => {
    const groups = groupFiles([
      file('bom.csv', 'bom', 'csv', 5),
      file('x.json', 'misc', 'json'),
      file('print/stl/a.stl', 'print', 'stl', 100),
      file('print/all_pieces.3mf', 'print', '3mf', 50),
      file('cad/assembly.step', 'cad', 'step'),
    ]);
    expect(groups.map((g) => g.key)).toEqual(['print', 'cad', 'bom', 'other']);
    expect(groups[0].sizeBytes).toBe(150);
    expect(groups[0].files).toHaveLength(2);
  });
});

describe('opensWith', () => {
  it('prefers the manifest text and falls back per file type', () => {
    expect(opensWith(file('a.stl', 'print', 'stl', 1, 'From the server.'))).toBe('From the server.');
    expect(opensWith(file('a.stl', 'print', 'stl'))).toMatch(/Bambu Studio, PrusaSlicer or Cura/);
    expect(opensWith(file('a.step', 'cad', 'step'))).toMatch(/FreeCAD/);
    expect(opensWith(file('a.dxf', 'drawings', 'dxf'))).toMatch(/LibreCAD/);
    expect(opensWith(file('a.pdf', 'drawings', 'pdf'))).toMatch(/PDF reader/);
    expect(opensWith(file('a.xyz', 'other', 'xyz'))).toMatch(/Download/);
  });
});

describe('pieces', () => {
  const part = (quantity: number, fits: boolean[]): ExportPart =>
    ({
      key: 'k',
      label: 'K',
      quantity,
      pieces: fits.map((f, i) => ({ number: i + 1, count: fits.length, label: `K ${i + 1}`, stl: `print/stl/k_${i + 1}.stl`, size_mm: [1, 2, 3], fits: f })),
    }) as ExportPart;

  it('piece id is the STL file stem', () => {
    expect(pieceId('print/stl/wing_right_01of02.stl')).toBe('wing_right_01of02');
  });

  it('counts copies and checks every piece fits', () => {
    expect(piecesToPrint(part(2, [true, true, true]))).toBe(6);
    expect(allPiecesFit({ schema: 'vtol-files/1', parts: [part(1, [true]), part(1, [true, false])] })).toBe(false);
    expect(allPiecesFit({ schema: 'vtol-files/1', parts: [part(1, [true])] })).toBe(true);
    expect(allPiecesFit(null)).toBeNull();
  });
});

describe('texts', () => {
  it('formats dimensions in millimetres', () => {
    expect(dimsText([240, 240, 240])).toBe('240 × 240 × 240 mm');
    expect(dimsText([50.25, 1, 2], 1)).toBe('50.3 × 1 × 2 mm');
    expect(dimsText(null)).toBe('—');
  });

  it('describes the source and status', () => {
    expect(exportSourceText({ source: 'draft', version_number: null })).toBe('the draft');
    expect(exportSourceText({ source: 'version', version_number: 3 })).toBe('v3');
    expect(exportStatusText({ status: 'running', progress: 0.42, queue_position: null, file_count: null })).toBe('Making the files (42 %)');
    expect(exportStatusText({ status: 'done', progress: 1, queue_position: null, file_count: 12 })).toBe('Ready: 12 files');
    expect(exportStatusText({ status: 'queued', progress: 0, queue_position: 2, file_count: null })).toMatch(/2 ahead/);
    expect(warningText({ message: 'Check the spar.' })).toBe('Check the spar.');
    expect(warningText('Plain.')).toBe('Plain.');
  });

  it('encodes each path segment of a download link', () => {
    expect(fileUrl(7, 'print/stl/a b.stl')).toBe('/api/exports/7/files/print/stl/a%20b.stl');
  });
});

describe('mesh', () => {
  it('decodes base64 float32 positions and uint32 indices', () => {
    const pos = new Float32Array([0, 0, 0, 10, 0, 0, 0, 20, 5]);
    const idx = new Uint32Array([0, 1, 2]);
    const b64 = (buf: ArrayBuffer) => btoa(String.fromCharCode(...new Uint8Array(buf)));
    const mesh = decodeMesh({ positions: b64(pos.buffer), indices: b64(idx.buffer) });
    expect(Array.from(mesh.positions)).toEqual(Array.from(pos));
    expect(Array.from(mesh.indices)).toEqual([0, 1, 2]);
  });

  it('centres the piece on the bed, resting on it', () => {
    expect(bedOffset([10, 20, 5], [30, 40, 15], [256, 256])).toEqual([108, 98, -5]);
  });
});
