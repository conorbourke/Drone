/**
 * One orthographic drawing (top, side or front) built from the engine's render primitives,
 * with dimension lines in millimetres, the balance points and neutral point, and drag
 * handles. Dragging a handle updates the draft live: the value snaps to 1 mm (10 mm with
 * Shift), stays inside the schema limits and is shown next to the handle while dragging.
 * Handles are also keyboard sliders (arrow keys, Shift for 10 mm).
 */
import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent as ReactPointerEvent } from 'react';
import type { DesignParameters, SchemaMap } from '../api/types';
import type { Estimates, Geometry, Vec3 } from '../engine';
import {
  boundsOf,
  handlePositions,
  padBounds,
  pointsAttr,
  project,
  unprojectDelta,
  VIEW_HANDLES,
  viewShapes,
  type Bounds,
  type Pt,
  type View,
} from '../lib/drawing';
import { applyHandle, HANDLE_LABEL, HANDLE_PATH, type Delta, type HandleName } from '../lib/handles';
import { getAtPath } from '../lib/draft';

const VIEW_TITLE: Record<View, string> = { top: 'Top view', side: 'Side view', front: 'Front view' };

interface Dim {
  /** End points in aircraft coordinates (mm). */
  a: Vec3;
  b: Vec3;
  /** Where the dimension line sits, outside the outline bounds. */
  side: 'above' | 'below' | 'left' | 'right';
  level: number;
  text: string;
}

function fmtMm(v: number): string {
  return `${Math.round(v)}`;
}

function dimensions(view: View, g: Geometry): Dim[] {
  const w = g.wing;
  const semi = w.span_mm / 2;
  const tip = w.tip_le;
  const boom = g.booms.find((b) => b.side === 'right');
  const dims: Dim[] = [];
  if (view === 'top') {
    // Nose up: x runs down the drawing, starboard to the right.
    dims.push({ a: [tip[0], -semi, 0], b: [tip[0], semi, 0], side: 'above', level: 0, text: `span ${fmtMm(w.span_mm)}` });
    dims.push({ a: [tip[0], semi, 0], b: [tip[0] + w.tip_chord_mm, semi, 0], side: 'right', level: 0, text: `tip ${fmtMm(w.tip_chord_mm)}` });
    dims.push({ a: [w.root_le[0], 0, 0], b: [w.root_le[0] + w.root_chord_mm, 0, 0], side: 'right', level: 1, text: `root ${fmtMm(w.root_chord_mm)}` });
    dims.push({ a: [0, 0, 0], b: [g.fuselage.length_mm, 0, 0], side: 'left', level: 0, text: `fuselage ${fmtMm(g.fuselage.length_mm)}` });
    dims.push({ a: [0, 0, 0], b: [w.root_le[0], 0, 0], side: 'left', level: 1, text: `wing at ${fmtMm(w.root_le[0])}` });
    dims.push({ a: [w.ac_x_mm, 0, 0], b: [g.tail.quarter_chord_x_mm, 0, 0], side: 'left', level: 2, text: `tail arm ${fmtMm(g.tail.arm_mm)}` });
    if (boom) dims.push({ a: [boom.end[0], 0, 0], b: [boom.end[0], boom.start[1], 0], side: 'below', level: 0, text: `boom ${fmtMm(boom.start[1])}` });
  } else if (view === 'side') {
    dims.push({ a: [0, 0, 0], b: [g.fuselage.length_mm, 0, 0], side: 'below', level: 0, text: `fuselage ${fmtMm(g.fuselage.length_mm)}` });
    dims.push({ a: [0, 0, 0], b: [w.root_le[0], 0, 0], side: 'below', level: 1, text: `wing at ${fmtMm(w.root_le[0])}` });
    dims.push({ a: [w.ac_x_mm, 0, 0], b: [g.tail.quarter_chord_x_mm, 0, 0], side: 'below', level: 2, text: `tail arm ${fmtMm(g.tail.arm_mm)}` });
  } else {
    dims.push({ a: [0, -semi, 0], b: [0, semi, 0], side: 'below', level: 0, text: `span ${fmtMm(w.span_mm)}` });
    if (boom) dims.push({ a: [0, -boom.start[1], 0], b: [0, boom.start[1], 0], side: 'above', level: 0, text: `booms ${fmtMm(2 * boom.start[1])} apart` });
  }
  return dims;
}

/** Value change per keyboard step along each handle's own axis. */
function keyDelta(name: HandleName, step: number): Delta {
  switch (name) {
    case 'span':
      return { dx: 0, dy: step / 2, dz: 0 };
    case 'boom_offset':
      return { dx: 0, dy: step, dz: 0 };
    case 'fuselage_length':
      return { dx: -step, dy: 0, dz: 0 };
    default:
      return { dx: step, dy: 0, dz: 0 };
  }
}

interface DragState {
  handle: HandleName;
  pointerId: number;
  start: Pt;
  params: DesignParameters;
  box: Bounds;
}

export interface DesignDrawingProps {
  view: View;
  geometry: Geometry;
  parameters: DesignParameters;
  estimates: Estimates | null;
  schema: SchemaMap | null;
  /** Apply parameter updates (paths without the "parameters." root). */
  onEdit?: (updates: [string, number][]) => void;
  /** Overlay mode (compare): outlines only, no handles, dims or marks. */
  className?: string;
}

export function DesignDrawing({ view, geometry: g, parameters, estimates, schema, onEdit }: DesignDrawingProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const drag = useRef<DragState | null>(null);
  const [frozen, setFrozen] = useState<Bounds | null>(null);
  const [label, setLabel] = useState<{ handle: HandleName; value: number; shift: number } | null>(null);
  // Width of the drawing on screen, so text, marks and handles keep a fixed pixel size.
  const figureRef = useRef<HTMLElement>(null);
  const [widthPx, setWidthPx] = useState(560);
  useEffect(() => {
    const el = figureRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w && w > 0) setWidthPx(w);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const shapes = viewShapes(view, g);
  const raw = boundsOf(shapes);
  // Millimetres per screen pixel: fit the outline plus a fixed pixel margin for the dimension
  // lines into the available width (and the CSS max-height of the drawing).
  const PAD_PX = 66;
  const maxHeightPx = view === 'top' ? 520 : 260;
  const rawW = Math.max(raw.maxU - raw.minU, 50);
  const rawH = Math.max(raw.maxV - raw.minV, 50);
  const avail = Math.max(widthPx - 2 * PAD_PX, 60);
  let pxPerMm = avail / rawW;
  if (rawH * pxPerMm + 2 * PAD_PX > maxHeightPx) pxPerMm = Math.max((maxHeightPx - 2 * PAD_PX) / rawH, 0.02);
  const mm = (px: number) => px / pxPerMm;
  const spacing = mm(17);
  const fit = padBounds(raw, mm(PAD_PX));
  const box = frozen ?? fit;
  const vbW = box.maxU - box.minU;
  const vbH = box.maxV - box.minV;
  const unit = mm(4); // marks, ticks and handles in screen pixels
  const fs = mm(11);
  // While the nose is dragged, shift the drawing so the rest of the aircraft stays put.
  const noseShift = label?.shift ?? 0;
  const shiftU = view === 'side' ? -noseShift : 0;
  const shiftV = view === 'top' ? -noseShift : 0;

  const toSvg = (clientX: number, clientY: number): Pt | null => {
    const svg = svgRef.current;
    const ctm = svg?.getScreenCTM();
    if (!svg || !ctm) return null;
    const p = new DOMPoint(clientX, clientY).matrixTransform(ctm.inverse());
    return [p.x, p.y];
  };

  const valueOf = (name: HandleName) => {
    const v = getAtPath(parameters, HANDLE_PATH[name]);
    return typeof v === 'number' ? v : Number.NaN;
  };

  const onPointerDown = (name: HandleName) => (event: ReactPointerEvent<SVGElement>) => {
    if (!onEdit || event.button !== 0) return;
    const start = toSvg(event.clientX, event.clientY);
    if (!start) return;
    event.preventDefault();
    event.stopPropagation();
    svgRef.current?.setPointerCapture(event.pointerId);
    drag.current = { handle: name, pointerId: event.pointerId, start, params: parameters, box: fit };
    setFrozen(fit);
    setLabel({ handle: name, value: valueOf(name), shift: 0 });
  };

  const onPointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (!d || event.pointerId !== d.pointerId || !onEdit) return;
    const p = toSvg(event.clientX, event.clientY);
    if (!p) return;
    const delta = unprojectDelta(view, p[0] - d.start[0], p[1] - d.start[1]);
    const result = applyHandle(d.handle, d.params, delta, event.shiftKey, schema);
    onEdit(result.updates);
    setLabel({ handle: d.handle, value: result.value, shift: result.noseShift });
  };

  const endDrag = (event: ReactPointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (!d || event.pointerId !== d.pointerId) return;
    drag.current = null;
    svgRef.current?.releasePointerCapture?.(event.pointerId);
    setFrozen(null);
    setLabel(null);
  };

  const onKeyDown = (name: HandleName) => (event: KeyboardEvent<SVGElement>) => {
    if (!onEdit) return;
    const step = event.shiftKey ? 10 : 1;
    let sign = 0;
    if (event.key === 'ArrowRight' || event.key === 'ArrowUp') sign = 1;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') sign = -1;
    if (!sign) return;
    event.preventDefault();
    const result = applyHandle(name, parameters, keyDelta(name, sign * step), false, schema);
    onEdit(result.updates);
  };

  const handles = handlePositions(g);
  const dims = dimensions(view, g);
  const cg = estimates?.balance;
  const marks: { x: number; kind: 'cg-heavy' | 'cg-light' | 'np'; title: string }[] = [];
  if (cg && view !== 'front') {
    marks.push({ x: cg.cg_max_payload_x.value, kind: 'cg-heavy', title: `Balance point with the heaviest camera: ${Math.round(cg.cg_max_payload_x.value)} mm from the nose` });
    marks.push({ x: cg.cg_min_payload_x.value, kind: 'cg-light', title: `Balance point with the lightest camera: ${Math.round(cg.cg_min_payload_x.value)} mm from the nose` });
    marks.push({ x: cg.neutral_point_x.value, kind: 'np', title: `Neutral point: ${Math.round(cg.neutral_point_x.value)} mm from the nose` });
  }

  const dimLine = (dim: Dim, i: number) => {
    const off = spacing * (1 + dim.level);
    const pa = project(view, dim.a);
    const pb = project(view, dim.b);
    const horizontal = dim.side === 'below' || dim.side === 'above';
    let a: Pt;
    let b: Pt;
    let textPos: Pt;
    if (horizontal) {
      const v = dim.side === 'below' ? raw.maxV + off : raw.minV - off;
      a = [pa[0], v];
      b = [pb[0], v];
      textPos = [(a[0] + b[0]) / 2, v - fs * 0.35];
    } else {
      const u = dim.side === 'left' ? raw.minU - off : raw.maxU + off;
      a = [u, pa[1]];
      b = [u, pb[1]];
      // Text on the outer side of the line, clear of the outline and the handles.
      textPos = [dim.side === 'left' ? u - fs * 0.35 : u + fs * 0.95, (a[1] + b[1]) / 2];
    }
    const tick = unit * 0.9;
    return (
      <g key={i} className="dim">
        <line x1={pa[0]} y1={pa[1]} x2={a[0]} y2={a[1]} className="dim-ext" />
        <line x1={pb[0]} y1={pb[1]} x2={b[0]} y2={b[1]} className="dim-ext" />
        <line x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} className="dim-line" />
        {[a, b].map((p, k) => (
          <line key={k} x1={p[0] - tick} y1={p[1] + tick} x2={p[0] + tick} y2={p[1] - tick} className="dim-line" />
        ))}
        <text
          x={textPos[0]}
          y={textPos[1]}
          fontSize={fs}
          textAnchor="middle"
          className="dim-text"
          transform={horizontal ? undefined : `rotate(-90 ${textPos[0]} ${textPos[1]})`}
        >
          {dim.text}
        </text>
      </g>
    );
  };

  const handleNames = VIEW_HANDLES[view] as HandleName[];
  const dragging = label !== null;

  return (
    <figure ref={figureRef} className={`drawing drawing-${view}${dragging ? ' is-dragging' : ''}`}>
      <figcaption className="drawing-title">
        <span>{VIEW_TITLE[view]}</span>
        <span className="small faint">mm</span>
      </figcaption>
      <svg
        ref={svgRef}
        data-testid={`drawing-${view}`}
        viewBox={`${box.minU} ${box.minV} ${vbW} ${vbH}`}
        preserveAspectRatio="xMidYMid meet"
        role="group"
        aria-label={`${VIEW_TITLE[view]} with dimensions in millimetres`}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        <g transform={shiftU || shiftV ? `translate(${shiftU} ${shiftV})` : undefined}>
          {shapes.map((s, i) =>
            s.open ? (
              <polyline key={i} points={pointsAttr(s.points)} className={`shape shape-${s.role}`} />
            ) : (
              <polygon key={i} points={pointsAttr(s.points)} className={`shape shape-${s.role}`} />
            ),
          )}
          {!dragging ? dims.map(dimLine) : null}
          {marks.map((m) => {
            const [u, v] = project(view, [m.x, 0, view === 'side' ? 0 : 0]);
            const r = unit * 1.3;
            if (m.kind === 'np') {
              return (
                <g key={m.kind} className="mark mark-np">
                  <title>{m.title}</title>
                  <path d={`M ${u} ${v - r * 0.2} l ${-r} ${-r * 1.6} h ${2 * r} z`} />
                </g>
              );
            }
            return (
              <g key={m.kind} className={`mark mark-${m.kind}`}>
                <title>{m.title}</title>
                <circle cx={u} cy={v} r={r} />
                <path d={`M ${u - r} ${v} H ${u + r} M ${u} ${v - r} V ${v + r}`} />
              </g>
            );
          })}
          {onEdit
            ? handleNames.map((name) => {
                const [u, v] = project(view, handles[name]);
                const value = valueOf(name);
                const active = label?.handle === name;
                return (
                  <g
                    key={name}
                    className={`handle${active ? ' handle-active' : ''}`}
                    data-testid={`handle-${name}`}
                    data-handle={name}
                    role="slider"
                    tabIndex={0}
                    aria-label={`${HANDLE_LABEL[name]} (drag, or use the arrow keys)`}
                    aria-valuenow={Math.round(value)}
                    aria-valuetext={`${Math.round(value)} mm`}
                    onPointerDown={onPointerDown(name)}
                    onKeyDown={onKeyDown(name)}
                  >
                    <title>{`${HANDLE_LABEL[name]}: ${Math.round(value)} mm. Drag to change; Shift snaps to 10 mm.`}</title>
                    <circle cx={u} cy={v} r={mm(11)} className="handle-hit" />
                    <circle cx={u} cy={v} r={unit * 1.25} className="handle-dot" />
                  </g>
                );
              })
            : null}
        </g>
        {label
          ? (() => {
              const [u, v] = project(view, handles[label.handle]);
              const text = `${HANDLE_LABEL[label.handle]} ${Math.round(label.value)} mm`;
              const w = text.length * fs * 0.56 + fs;
              const x = Math.min(Math.max(u + shiftU - w / 2, box.minU), box.maxU - w);
              const y = Math.max(v + shiftV - unit * 4 - fs * 1.4, box.minV);
              return (
                <g className="drag-label" data-testid="drag-label" pointerEvents="none">
                  <rect x={x} y={y} width={w} height={fs * 1.5} rx={fs * 0.3} />
                  <text x={x + w / 2} y={y + fs * 1.08} fontSize={fs} textAnchor="middle">
                    {text}
                  </text>
                </g>
              );
            })()
          : null}
      </svg>
    </figure>
  );
}
