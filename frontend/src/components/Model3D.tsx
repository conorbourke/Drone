/**
 * 3D view of the design (three.js, lazy-loaded so the first page stays small). Built from the
 * engine's render primitives: wing and tail lofted from airfoil sections, the fuselage lofted
 * through its stations with the nose bay highlighted, booms, motors, propeller discs, tilt
 * hinges and landing gear. Orbit with the mouse or a finger; drag the orange handles to edit
 * span, chords, wing position, boom offset, tail arm and fuselage length.
 *
 * Aircraft axes (x aft, y starboard, z up, mm) map to three.js as (x, z, -y) so "up" is +Y.
 */
import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import type { DesignParameters, SchemaMap } from '../api/types';
import type { Geometry, Vec3 } from '../engine';
import { handlePositions } from '../lib/drawing';
import { applyHandle, HANDLE_LABEL, HANDLE_NAMES, type HandleName } from '../lib/handles';

export interface Model3DProps {
  geometry: Geometry;
  parameters: DesignParameters;
  schema: SchemaMap | null;
  onEdit?: (updates: [string, number][]) => void;
}

const toThree = (p: Vec3) => new THREE.Vector3(p[0], p[2], -p[1]);

interface Palette {
  body: number;
  wing: number;
  bay: number;
  boom: number;
  motor: number;
  prop: number;
  gear: number;
  handle: number;
  handleActive: number;
}

function palette(): Palette {
  const dark = typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: dark)').matches;
  return dark
    ? { body: 0x9aa7b6, wing: 0xc6d0dc, bay: 0x3987e5, boom: 0x5b6676, motor: 0x3a424d, prop: 0x8bbbe8, gear: 0x76818f, handle: 0xd95926, handleActive: 0xffd166 }
    : { body: 0xb9c2cd, wing: 0xe4e9ef, bay: 0x2a78d6, boom: 0x4a5462, motor: 0x2c333c, prop: 0x2a78d6, gear: 0x5b6774, handle: 0xeb6834, handleActive: 0xb3261e };
}

/** Lofted surface between closed sections of equal point count, with end caps. */
function loft(sections: THREE.Vector3[][]): THREE.BufferGeometry {
  const n = sections[0].length;
  const positions: number[] = [];
  const index: number[] = [];
  for (const s of sections) for (const p of s) positions.push(p.x, p.y, p.z);
  for (let k = 0; k < sections.length - 1; k++) {
    for (let i = 0; i < n; i++) {
      const a = k * n + i;
      const b = k * n + ((i + 1) % n);
      const c = (k + 1) * n + ((i + 1) % n);
      const d = (k + 1) * n + i;
      index.push(a, b, c, a, c, d);
    }
  }
  for (const k of [0, sections.length - 1]) {
    const centre = sections[k].reduce((acc, p) => acc.add(p), new THREE.Vector3()).multiplyScalar(1 / n);
    const ci = positions.length / 3;
    positions.push(centre.x, centre.y, centre.z);
    for (let i = 0; i < n; i++) {
      const a = k * n + i;
      const b = k * n + ((i + 1) % n);
      if (k === 0) index.push(ci, b, a);
      else index.push(ci, a, b);
    }
  }
  const geom = new THREE.BufferGeometry();
  geom.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geom.setIndex(index);
  geom.computeVertexNormals();
  return geom;
}

function ring(x: number, w: number, h: number, kind: 'ellipse' | 'rounded_rect', n = 32): THREE.Vector3[] {
  const out: THREE.Vector3[] = [];
  for (let i = 0; i < n; i++) {
    const t = (2 * Math.PI * i) / n;
    const c = Math.cos(t);
    const s = Math.sin(t);
    const e = kind === 'ellipse' ? 1 : 0.4;
    const yy = (w / 2) * Math.sign(c) * Math.abs(c) ** e;
    const zz = (h / 2) * Math.sign(s) * Math.abs(s) ** e;
    out.push(toThree([x, yy, zz]));
  }
  return out;
}

function cylinder(a: THREE.Vector3, b: THREE.Vector3, radius: number, material: THREE.Material, segments = 16): THREE.Mesh {
  const dir = new THREE.Vector3().subVectors(b, a);
  const len = Math.max(dir.length(), 1e-3);
  const geom = new THREE.CylinderGeometry(radius, radius, len, segments);
  const mesh = new THREE.Mesh(geom, material);
  mesh.position.copy(a).addScaledVector(dir, 0.5);
  mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.normalize());
  return mesh;
}

function buildModel(g: Geometry, colors: Palette): THREE.Group {
  const group = new THREE.Group();
  const mat = (color: number, extra: Partial<THREE.MeshStandardMaterialParameters> = {}) =>
    new THREE.MeshStandardMaterial({ color, roughness: 0.6, metalness: 0.05, side: THREE.DoubleSide, ...extra });
  const r = g.render;

  for (const surface of r.surfaces) {
    const sections = surface.sections.map((s) => s.map(toThree));
    if (sections.length < 2 || sections[0].length < 3) continue;
    group.add(new THREE.Mesh(loft(sections), mat(colors.wing)));
  }

  const st = r.fuselage.stations;
  if (st.length >= 2) {
    const x1 = Math.min(r.fuselage.nose_bay_x1_mm, st[st.length - 1].x_mm);
    const bay = st.filter((s) => s.x_mm <= x1);
    const rest = st.filter((s) => s.x_mm >= x1);
    const i = st.findIndex((s) => s.x_mm >= x1);
    if (i > 0 && st[i].x_mm > x1) {
      const a = st[i - 1];
      const b = st[i];
      const t = (x1 - a.x_mm) / (b.x_mm - a.x_mm || 1);
      const mid = { x_mm: x1, width_mm: a.width_mm + (b.width_mm - a.width_mm) * t, height_mm: a.height_mm + (b.height_mm - a.height_mm) * t, perimeter_mm: 0 };
      bay.push(mid);
      rest.unshift(mid);
    }
    const kind = r.fuselage.cross_section;
    const toRings = (list: typeof st) => list.map((s) => ring(s.x_mm, Math.max(s.width_mm, 0.5), Math.max(s.height_mm, 0.5), kind));
    if (bay.length >= 2) group.add(new THREE.Mesh(loft(toRings(bay)), mat(colors.bay)));
    if (rest.length >= 2) group.add(new THREE.Mesh(loft(toRings(rest)), mat(colors.body)));
  }

  const boomMat = mat(colors.boom, { roughness: 0.4 });
  for (const b of r.booms) group.add(cylinder(toThree(b.start), toThree(b.end), b.diameter_mm / 2, boomMat));
  for (const s of r.tail_supports) group.add(cylinder(toThree(s.start), toThree(s.end), s.diameter_mm / 2, boomMat));

  const motorMat = mat(colors.motor, { metalness: 0.4, roughness: 0.35 });
  r.motors.forEach((m, i) => {
    const axis = r.props[i]?.axis ?? [0, 0, 1];
    const top = toThree(m.position);
    const base = toThree([m.position[0] - axis[0] * m.height_mm, m.position[1] - axis[1] * m.height_mm, m.position[2] - axis[2] * m.height_mm]);
    group.add(cylinder(base, top, m.diameter_mm / 2, motorMat, 20));
  });

  const propMat = new THREE.MeshStandardMaterial({ color: colors.prop, transparent: true, opacity: 0.22, side: THREE.DoubleSide, depthWrite: false });
  const propEdge = new THREE.LineBasicMaterial({ color: colors.prop, transparent: true, opacity: 0.7 });
  for (const p of r.props) {
    const disc = new THREE.Mesh(new THREE.CircleGeometry(p.diameter_mm / 2, 48), propMat);
    const edge = new THREE.LineLoop(
      new THREE.BufferGeometry().setFromPoints(
        Array.from({ length: 48 }, (_, k) => new THREE.Vector3(Math.cos((k / 48) * Math.PI * 2) * (p.diameter_mm / 2), Math.sin((k / 48) * Math.PI * 2) * (p.diameter_mm / 2), 0)),
      ),
      propEdge,
    );
    const holder = new THREE.Group();
    holder.add(disc, edge);
    holder.position.copy(toThree(p.centre));
    holder.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), toThree(p.axis).normalize());
    group.add(holder);
  }

  const hingeMat = mat(colors.handleActive);
  for (const h of r.tilt_hinges) {
    const c = toThree(h.position);
    group.add(cylinder(c.clone().add(new THREE.Vector3(0, 0, 18)), c.clone().add(new THREE.Vector3(0, 0, -18)), 8, hingeMat, 12));
  }

  const gearMat = mat(colors.gear);
  for (const seg of r.landing_gear.segments) group.add(cylinder(toThree(seg.start), toThree(seg.end), 3, gearMat, 8));
  return group;
}

function dispose(object: THREE.Object3D) {
  object.traverse((o) => {
    const mesh = o as THREE.Mesh;
    mesh.geometry?.dispose();
    const m = mesh.material as THREE.Material | THREE.Material[] | undefined;
    if (Array.isArray(m)) m.forEach((x) => x.dispose());
    else m?.dispose();
  });
}

interface Scene {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
  root: THREE.Group;
  model: THREE.Group | null;
  handles: THREE.Group;
  render: () => void;
  colors: Palette;
}

interface Drag {
  name: HandleName;
  pointerId: number;
  plane: THREE.Plane;
  start: THREE.Vector3;
  params: DesignParameters;
  shift: number;
}

export default function Model3D({ geometry, parameters, schema, onEdit }: Model3DProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<Scene | null>(null);
  const dragRef = useRef<Drag | null>(null);
  const fitted = useRef(false);
  const latest = useRef({ parameters, schema, onEdit, geometry });
  const [failed, setFailed] = useState<string | null>(null);
  const [label, setLabel] = useState<{ text: string; x: number; y: number } | null>(null);

  useEffect(() => {
    latest.current = { parameters, schema, onEdit, geometry };
  });

  // Renderer, camera, controls and pointer handling: once.
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch {
      // Reported from a microtask: the effect only wires up the external renderer.
      queueMicrotask(() =>
        setFailed('This browser cannot show 3D graphics (WebGL is not available). The drawings below show the same design.'),
      );
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    host.appendChild(renderer.domElement);
    renderer.domElement.style.touchAction = 'none';
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(35, 1, 10, 200000);
    camera.position.set(-1500, 900, 1600);
    const controls = new OrbitControls(camera, renderer.domElement);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 1.6));
    const sun = new THREE.DirectionalLight(0xffffff, 1.8);
    sun.position.set(-1, 2, 1.5);
    scene.add(sun);
    const root = new THREE.Group();
    const handles = new THREE.Group();
    root.add(handles);
    scene.add(root);

    let frame = 0;
    const render = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        renderer.render(scene, camera);
      });
    };
    controls.addEventListener('change', render);
    const s: Scene = { renderer, scene, camera, controls, root, model: null, handles, render, colors: palette() };
    sceneRef.current = s;

    const resize = () => {
      const w = host.clientWidth;
      const h = host.clientHeight;
      if (!w || !h) return;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      render();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    resize();

    const raycaster = new THREE.Raycaster();
    const ndc = (event: PointerEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      return new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    };
    const pick = (event: PointerEvent): THREE.Object3D | null => {
      raycaster.setFromCamera(ndc(event), camera);
      const hit = raycaster.intersectObjects(handles.children, false)[0];
      return hit?.object ?? null;
    };
    const planePoint = (event: PointerEvent, plane: THREE.Plane) => {
      raycaster.setFromCamera(ndc(event), camera);
      return raycaster.ray.intersectPlane(plane, new THREE.Vector3());
    };
    const screenOf = (world: THREE.Vector3) => {
      const v = world.clone().project(camera);
      return { x: ((v.x + 1) / 2) * host.clientWidth, y: ((1 - v.y) / 2) * host.clientHeight };
    };
    const setHighlight = (name: string | null) => {
      for (const child of handles.children) {
        const m = (child as THREE.Mesh).material as THREE.MeshBasicMaterial;
        m.color.setHex(child.name === name ? s.colors.handleActive : s.colors.handle);
      }
      render();
    };

    const onDown = (event: PointerEvent) => {
      if (!latest.current.onEdit || event.button !== 0) return;
      const hit = pick(event);
      if (!hit) return;
      const name = hit.name as HandleName;
      const world = hit.getWorldPosition(new THREE.Vector3());
      const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(new THREE.Vector3(0, 1, 0), world);
      const start = planePoint(event, plane);
      if (!start) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      controls.enabled = false;
      renderer.domElement.setPointerCapture(event.pointerId);
      dragRef.current = { name, pointerId: event.pointerId, plane, start, params: latest.current.parameters, shift: 0 };
      setHighlight(name);
      const p = screenOf(world);
      setLabel({ text: `${HANDLE_LABEL[name]}`, x: p.x, y: p.y });
    };
    const onMove = (event: PointerEvent) => {
      const d = dragRef.current;
      if (!d) {
        const hit = pick(event);
        renderer.domElement.style.cursor = hit ? 'grab' : '';
        return;
      }
      if (event.pointerId !== d.pointerId) return;
      const p = planePoint(event, d.plane);
      const { onEdit: edit, schema: sch } = latest.current;
      if (!p || !edit) return;
      const delta = { dx: p.x - d.start.x, dy: -(p.z - d.start.z), dz: 0 };
      const result = applyHandle(d.name, d.params, delta, event.shiftKey, sch);
      // Pulling the nose: keep the rest of the aircraft still on screen.
      root.position.x -= result.noseShift - d.shift;
      d.shift = result.noseShift;
      edit(result.updates);
      const screen = screenOf(new THREE.Vector3(p.x, p.y, p.z));
      setLabel({ text: `${HANDLE_LABEL[d.name]} ${Math.round(result.value)} mm`, x: screen.x, y: screen.y });
    };
    const onUp = (event: PointerEvent) => {
      const d = dragRef.current;
      if (!d || event.pointerId !== d.pointerId) return;
      dragRef.current = null;
      controls.enabled = true;
      renderer.domElement.releasePointerCapture?.(event.pointerId);
      setHighlight(null);
      setLabel(null);
    };
    // Capture phase so a handle grab wins over OrbitControls.
    renderer.domElement.addEventListener('pointerdown', onDown, { capture: true });
    renderer.domElement.addEventListener('pointermove', onMove);
    renderer.domElement.addEventListener('pointerup', onUp);
    renderer.domElement.addEventListener('pointercancel', onUp);

    const media = window.matchMedia?.('(prefers-color-scheme: dark)');
    const onScheme = () => {
      s.colors = palette();
      fitted.current = true;
      rebuild(s, latest.current.geometry);
    };
    media?.addEventListener?.('change', onScheme);

    return () => {
      media?.removeEventListener?.('change', onScheme);
      observer.disconnect();
      cancelAnimationFrame(frame);
      controls.dispose();
      if (s.model) dispose(s.model);
      dispose(handles);
      renderer.dispose();
      renderer.domElement.remove();
      sceneRef.current = null;
    };
  }, []);

  // Rebuild the model whenever the geometry changes (every edit; a few milliseconds).
  useEffect(() => {
    const s = sceneRef.current;
    if (!s) return;
    rebuild(s, geometry);
    if (!fitted.current) {
      fitted.current = true;
      fitView(s);
    }
  }, [geometry]);

  const resetView = () => {
    const s = sceneRef.current;
    if (!s) return;
    s.root.position.set(0, 0, 0);
    fitView(s);
  };

  return (
    <div className="model3d" data-testid="view-3d">
      <div ref={hostRef} className="model3d-canvas" aria-label="3D view of the design. Drag to orbit, scroll or pinch to zoom; drag the orange handles to edit." role="img" />
      {failed ? <p className="model3d-fallback small muted">{failed}</p> : null}
      {label ? (
        <div className="model3d-label" style={{ left: label.x, top: label.y }} data-testid="drag-label-3d">
          {label.text}
        </div>
      ) : null}
      {!failed ? (
        <div className="model3d-toolbar">
          <span className="small muted model3d-hint">Drag to orbit · orange dots edit</span>
          <button type="button" className="button button-sm" onClick={resetView}>
            Reset view
          </button>
        </div>
      ) : null}
    </div>
  );
}

function rebuild(s: Scene, g: Geometry) {
  if (s.model) {
    s.root.remove(s.model);
    dispose(s.model);
  }
  s.model = buildModel(g, s.colors);
  s.root.add(s.model);
  // Handles: always drawn on top so they can be grabbed through the wing.
  dispose(s.handles);
  s.handles.clear();
  const positions = handlePositions(g);
  const radius = Math.max(g.wing.span_mm, g.fuselage.length_mm) * 0.012;
  for (const name of HANDLE_NAMES) {
    const mesh = new THREE.Mesh(
      new THREE.SphereGeometry(radius, 20, 14),
      new THREE.MeshBasicMaterial({ color: s.colors.handle, depthTest: false, transparent: true, opacity: 0.95 }),
    );
    mesh.name = name;
    mesh.renderOrder = 10;
    mesh.position.copy(toThree(positions[name]));
    s.handles.add(mesh);
  }
  s.render();
}

function fitView(s: Scene) {
  const box = new THREE.Box3().setFromObject(s.model ?? s.root);
  if (box.isEmpty()) return;
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  // Fit the bounding sphere to the narrower of the two fields of view, a little tight (the
  // sphere is larger than the aircraft's silhouette from this angle).
  const half = THREE.MathUtils.degToRad(s.camera.fov / 2);
  const halfH = Math.atan(Math.tan(half) * s.camera.aspect);
  const dist = (sphere.radius / Math.sin(Math.min(half, halfH))) * 0.72;
  const dir = new THREE.Vector3(-0.9, 0.65, 1).normalize();
  s.controls.target.copy(sphere.center);
  s.camera.position.copy(sphere.center).addScaledVector(dir, dist);
  s.camera.near = Math.max(1, dist / 100);
  s.camera.far = dist * 20;
  s.camera.updateProjectionMatrix();
  s.controls.update();
  s.render();
}
