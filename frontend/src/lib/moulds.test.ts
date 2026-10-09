import { describe, expect, it } from 'vitest';
import type { DraftMap, MouldDraft, MouldFile, MouldPart } from '../api/moulds';
import { mouldFileUrl } from '../api/moulds';
import {
  degText,
  draftBin,
  draftDots,
  draftSummaryText,
  flaggedFaceText,
  groupMouldFiles,
  motorOutVerdict,
  mouldPartsText,
  mouldStatusText,
  partMassG,
  partTiles,
  percentText,
  plyOrientationText,
  tileId,
  utilisationText,
} from './moulds';

const f = (path: string, kind: string, size = 10): MouldFile => ({ path, kind, size_bytes: size, part: 'nose' });

describe('groupMouldFiles', () => {
  it('orders STL, 3MF, STEP, PDF with opens-with notes and puts unknown types last', () => {
    const groups = groupMouldFiles([
      f('nose/nose_mould_sheet.pdf', 'pdf', 5),
      f('nose/x.json', 'json'),
      f('nose/tiles/NOSE-R-01of01.stl', 'stl', 100),
      f('nose/tiles/NOSE-L-01of01.stl', 'stl', 50),
      f('nose/nose_mould_right.step', 'step'),
      f('nose/tiles/NOSE-R-01of01.3mf', '3mf'),
    ]);
    expect(groups.map((g) => g.key)).toEqual(['stl', '3mf', 'step', 'pdf', 'other']);
    expect(groups[0].sizeBytes).toBe(150);
    expect(groups[0].opensWith).toMatch(/Bambu Studio/);
    expect(groups[2].opensWith).toMatch(/FreeCAD/);
  });
});

describe('tile ids and urls', () => {
  it('uses the STL stem and encodes each path segment', () => {
    expect(tileId('fuselage/tiles/FUS-L-02of08.stl')).toBe('FUS-L-02of08');
    expect(mouldFileUrl(3, 'nose/tiles/a b.stl')).toBe('/api/moulds/3/files/nose/tiles/a%20b.stl');
  });
});

describe('numbers in words', () => {
  it('formats percent and degrees, negative zero as 0°', () => {
    expect(percentText(0.2346)).toBe('23\u202f%');
    expect(percentText(null)).toBe('—');
    expect(degText(-0.0)).toBe('0°');
    expect(degText(2.09)).toBe('2.1°');
    expect(utilisationText(0.8214)).toBe('82\u202f%');
    expect(utilisationText(null)).toBe('—');
  });
});

const draft = (flagged: number[]): MouldDraft => ({
  min_draft_deg: 2,
  summary: { faces: 10, flagged: flagged.length, fraction_below_min_outside_band: 0.2346 },
  flagged_faces: flagged,
});

describe('draft text', () => {
  it('explains flagged faces plainly and praises a clean part', () => {
    expect(draftSummaryText(draft([]))).toMatch(/Every face has at least 2°/);
    const text = draftSummaryText(draft([2, 3]));
    expect(text).toMatch(/^2 faces have less than 2° of draft/);
    expect(text).toMatch(/23\u202f% of the surface/);
    expect(flaggedFaceText({ where: 'x 0-148 mm', min_draft_deg: -0.0, area_below_min_mm2: 6206.8 }, 2)).toBe(
      'x 0-148 mm: as little as 0° of draft; 62\u202fcm² is below 2°.',
    );
  });

  it('bins draft samples', () => {
    expect(draftBin(0.1, 2, true)).toBe('band');
    expect(draftBin(1, 2, false)).toBe('flag');
    expect(draftBin(3, 2, false)).toBe('low');
    expect(draftBin(5, 2, false)).toBe('ok');
  });

  it('scales one half of the draft map into the box', () => {
    const map: DraftMap = {
      points_uv_mm: [
        [0, 0],
        [100, 50],
        [50, 25],
      ],
      draft_deg: [5, 1, 3],
      half: ['right', 'right', 'left'],
      in_band: [false, false, false],
    };
    const dots = draftDots(map, 'right', 2, 208, 108);
    expect(dots).toHaveLength(2);
    expect(dots[0]).toEqual({ x: 4, y: 104, bin: 'ok' });
    expect(dots[1]).toEqual({ x: 204, y: 4, bin: 'flag' });
    expect(draftDots(map, 'upper', 2, 100, 100)).toEqual([]);
  });
});

describe('parts and status', () => {
  const part = {
    key: 'nose',
    label: 'Nose',
    draft: draft([]),
    halves: [
      { key: 'right', label: 'R', tiles: [{ estimated_mass_g: 100 }, { estimated_mass_g: 50 }] },
      { key: 'left', label: 'L', tiles: [{ estimated_mass_g: 25 }] },
    ],
  } as unknown as MouldPart;

  it('counts tiles and filament', () => {
    expect(partTiles(part)).toBe(3);
    expect(partMassG(part)).toBe(175);
  });

  it('names the parts and the job state', () => {
    expect(mouldPartsText(['nose'])).toBe('nose');
    expect(mouldPartsText(['nose', 'fuselage', 'wing_root_fairing'])).toBe('nose, fuselage and wing-root fairing');
    expect(mouldStatusText({ status: 'running', progress: 0.42, queue_position: null, summary: null })).toBe('Making the moulds (42 %)');
    expect(mouldStatusText({ status: 'queued', progress: 0, queue_position: 2, summary: null })).toBe('Waiting (2 ahead in the queue)');
    expect(mouldStatusText({ status: 'error', progress: 0, queue_position: null, summary: null })).toBe('Failed');
  });

  it('words the motor-out verdicts', () => {
    expect(motorOutVerdict({ holds_attitude_and_heading: true, holds_attitude_spinning: true })).toBe('Holds attitude and heading');
    expect(motorOutVerdict({ holds_attitude_and_heading: false, holds_attitude_spinning: true })).toBe('Stays level but spins');
    expect(motorOutVerdict({ holds_attitude_and_heading: false, holds_attitude_spinning: false })).toBe('Cannot stay level');
  });
});

describe('plyOrientationText', () => {
  it('adds degrees and the plus-minus sign', () => {
    expect(plyOrientationText('+-45')).toBe('±45°');
    expect(plyOrientationText('0/90')).toBe('0/90°');
    expect(plyOrientationText('0 (spanwise)')).toBe('0° (spanwise)');
  });
});
