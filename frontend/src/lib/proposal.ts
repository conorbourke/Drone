/**
 * Merging the rows the owner selected from a Claude proposal into the draft parameters.
 *
 * A subset of a consistent proposal can be inconsistent with the rest of the draft (a new tip
 * chord wider than the current root chord, a new root chord that runs past the current
 * fuselage end). The server rejects such a document, and every autosave after it would fail,
 * so the merge applies the same rules as the server (backend/app/schemas/design.py) and the
 * drag handles (lib/handles.ts): the schema limits, tip chord at most the root chord, wing root
 * inside the fuselage, rear motors behind the front motors. Values are clamped where there is
 * an obvious fix, with a plain note for each; otherwise the merge is refused with a message.
 */
import type { DesignParameters, SchemaMap } from '../api/types';
import { cloneJson, getAtPath, setAtPath } from './draft';
import { clampToSchema } from './ranges';

export interface ProposalSelection {
  /** Dotted parameter path without "parameters.". */
  path: string;
  value: unknown;
}

export interface ProposalMerge {
  /** The values to write, as [dotted path without "parameters.", value]; empty when blocked. */
  updates: [string, unknown][];
  /** Plain notes for every value that was changed from what Claude proposed or kept. */
  adjustments: string[];
  /** Set when no valid draft could be made; nothing should be applied. */
  blocked: string | null;
}

const fmt = (mm: number) => `${Math.round(mm * 10) / 10} mm`;

function num(p: DesignParameters, path: string): number {
  const v = getAtPath(p, path);
  return typeof v === 'number' && Number.isFinite(v) ? v : 0;
}

/**
 * Apply `selections` to `parameters`, then make the result pass the server's checks.
 * Adjustments, in order: each number into its schema limits; the wing moved forward (then the
 * root chord shortened) so the root fits inside the fuselage; the tip chord cut to the root
 * chord. A rear motor that is not behind the front motor cannot be fixed by a guess, so that
 * blocks the merge.
 */
export function mergeProposal(
  parameters: DesignParameters,
  selections: ProposalSelection[],
  schema: SchemaMap | null | undefined,
): ProposalMerge {
  let merged = cloneJson(parameters);
  const adjustments: string[] = [];
  const label = (path: string, fallback: string) => schema?.[path]?.label ?? fallback;

  for (const { path, value } of selections) {
    let next = value;
    if (typeof value === 'number') {
      const meta = schema?.[path];
      const clamped = clampToSchema(value, meta, 1);
      if (clamped !== value) {
        adjustments.push(
          `${label(path, path)}: ${value} is outside the allowed range; set to ${clamped}${meta?.unit ? ` ${meta.unit}` : ''}.`,
        );
        next = clamped;
      }
    }
    merged = setAtPath(merged, path, next);
  }

  const set = (path: string, value: number) => {
    merged = setAtPath(merged, path, value);
  };

  // Wing root inside the fuselage: x_le + root chord <= fuselage length.
  const length = num(merged, 'fuselage.length_mm');
  let xle = num(merged, 'wing.x_le_mm');
  let root = num(merged, 'wing.root_chord_mm');
  if (xle + root > length) {
    const newXle = Math.max(0, length - root);
    if (newXle !== xle) {
      adjustments.push(
        `${label('wing.x_le_mm', 'Wing position')}: moved from ${fmt(xle)} to ${fmt(newXle)} so the wing root ends inside the ${fmt(length)} fuselage.`,
      );
      xle = newXle;
      set('wing.x_le_mm', xle);
    }
    if (xle + root > length) {
      const newRoot = Math.floor(length - xle);
      if (!(newRoot >= 1)) {
        return {
          updates: [],
          adjustments: [],
          blocked:
            `These values cannot be applied together: the wing root chord does not fit inside a ${fmt(length)} fuselage. ` +
            'Select the fuselage length too, or change it first.',
        };
      }
      adjustments.push(
        `${label('wing.root_chord_mm', 'Root chord')}: shortened from ${fmt(root)} to ${fmt(newRoot)} to fit inside the ${fmt(length)} fuselage.`,
      );
      root = newRoot;
      set('wing.root_chord_mm', root);
    }
  }

  // Tip chord at most the root chord.
  const tip = num(merged, 'wing.tip_chord_mm');
  if (tip > root) {
    adjustments.push(
      `${label('wing.tip_chord_mm', 'Tip chord')}: ${fmt(tip)} is wider than the ${fmt(root)} root chord; set equal to it.`,
    );
    set('wing.tip_chord_mm', root);
  }

  // Rear motors behind the front motors.
  const front = num(merged, 'motors.front_x_mm');
  const rear = num(merged, 'motors.rear_x_mm');
  if (rear <= front) {
    return {
      updates: [],
      adjustments: [],
      blocked:
        `These values cannot be applied together: the rear motors (${fmt(rear)}) would not be behind the front motors (${fmt(front)}). ` +
        'Select both motor positions, or neither.',
    };
  }

  const updates: [string, unknown][] = [];
  const paths = new Set([
    ...selections.map((s) => s.path),
    'wing.x_le_mm',
    'wing.root_chord_mm',
    'wing.tip_chord_mm',
  ]);
  for (const path of paths) {
    const after = getAtPath(merged, path);
    if (after !== getAtPath(parameters, path) || selections.some((s) => s.path === path)) {
      updates.push([path, after]);
    }
  }
  return { updates, adjustments, blocked: null };
}
