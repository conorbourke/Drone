/**
 * 3D preview of one printed piece in its print orientation, standing on the printer bed
 * outline with the usable print envelope drawn as a wire box (three.js, lazy-loaded by the
 * Files tab). Print coordinates are millimetres with z up; the camera uses z as "up" so they
 * are drawn as they are. The WebGL context is released on unmount.
 */
import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import type { PieceMesh } from '../api/exports';
import { bedOffset, decodeMesh, dimsText } from '../lib/files';

export interface PiecePreview3DProps {
  mesh: PieceMesh;
}

interface Palette {
  piece: number;
  pieceBad: number;
  bed: number;
  bedLine: number;
  grid: number;
  envelope: number;
}

function palette(): Palette {
  const dark = typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: dark)').matches;
  return dark
    ? { piece: 0x5ea3d8, pieceBad: 0xe5736b, bed: 0x1f262f, bedLine: 0x8a95a3, grid: 0x2c3542, envelope: 0x1baf7a }
    : { piece: 0x2a78d6, pieceBad: 0xb3261e, bed: 0xe9eef4, bedLine: 0x5b6774, grid: 0xc9d1db, envelope: 0x1b7f4b };
}

function disposeTree(object: THREE.Object3D) {
  object.traverse((child) => {
    const item = child as THREE.Mesh;
    item.geometry?.dispose();
    const m = item.material as THREE.Material | THREE.Material[] | undefined;
    if (Array.isArray(m)) m.forEach((x) => x.dispose());
    else m?.dispose();
  });
}

/** Bed plate, its outline and a 10 mm grid (every 50 mm darker), at z = 0. */
function buildBed(bed: number[], colors: Palette): THREE.Group {
  const group = new THREE.Group();
  const [bx, by] = bed;
  const plate = new THREE.Mesh(
    new THREE.PlaneGeometry(bx, by),
    new THREE.MeshBasicMaterial({ color: colors.bed, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }),
  );
  plate.position.set(bx / 2, by / 2, 0);
  group.add(plate);
  const grid: number[] = [];
  for (let x = 50; x < bx; x += 50) grid.push(x, 0, 0, x, by, 0);
  for (let y = 50; y < by; y += 50) grid.push(0, y, 0, bx, y, 0);
  const gridGeom = new THREE.BufferGeometry();
  gridGeom.setAttribute('position', new THREE.Float32BufferAttribute(grid, 3));
  group.add(new THREE.LineSegments(gridGeom, new THREE.LineBasicMaterial({ color: colors.grid })));
  const outline = new THREE.BufferGeometry();
  outline.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, bx, 0, 0, bx, 0, 0, bx, by, 0, bx, by, 0, 0, by, 0, 0, by, 0, 0, 0, 0], 3));
  group.add(new THREE.LineSegments(outline, new THREE.LineBasicMaterial({ color: colors.bedLine })));
  return group;
}

/** The usable envelope as a dashed wire box centred on the bed. */
function buildEnvelope(env: number[], bed: number[], colors: Palette): THREE.LineSegments {
  const box = new THREE.BoxGeometry(env[0], env[1], env[2]);
  const edges = new THREE.EdgesGeometry(box);
  box.dispose();
  const lines = new THREE.LineSegments(edges, new THREE.LineDashedMaterial({ color: colors.envelope, dashSize: 6, gapSize: 4 }));
  lines.computeLineDistances();
  lines.position.set(bed[0] / 2, bed[1] / 2, env[2] / 2);
  return lines;
}

function buildPiece(mesh: PieceMesh, bed: number[], colors: Palette): THREE.Mesh {
  const { positions, indices } = decodeMesh(mesh);
  const [ox, oy, oz] = bedOffset(mesh.bounds_mm.min, mesh.bounds_mm.max, bed);
  for (let i = 0; i < positions.length; i += 3) {
    positions[i] += ox;
    positions[i + 1] += oy;
    positions[i + 2] += oz;
  }
  const geom = new THREE.BufferGeometry();
  geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geom.setIndex(new THREE.BufferAttribute(indices, 1));
  geom.computeVertexNormals();
  return new THREE.Mesh(
    geom,
    new THREE.MeshStandardMaterial({ color: mesh.fits === false ? colors.pieceBad : colors.piece, roughness: 0.65, metalness: 0.05, flatShading: true }),
  );
}

export default function PiecePreview3D({ mesh }: PiecePreview3DProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch {
      queueMicrotask(() => setFailed(true));
      return;
    }
    const colors = palette();
    const env = mesh.envelope_mm && mesh.envelope_mm.length === 3 ? mesh.envelope_mm : [240, 240, 240];
    const bed = mesh.bed_mm && mesh.bed_mm.length >= 2 ? mesh.bed_mm : [env[0], env[1]];

    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.domElement.className = 'model3d-canvas';
    renderer.domElement.style.touchAction = 'none';
    renderer.domElement.setAttribute('aria-hidden', 'true');
    host.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(35, 1, 1, 20000);
    camera.up.set(0, 0, 1);
    const controls = new OrbitControls(camera, renderer.domElement);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 1.5));
    const sun = new THREE.DirectionalLight(0xffffff, 1.8);
    sun.position.set(-1, -1.5, 2);
    scene.add(sun);
    const root = new THREE.Group();
    root.add(buildBed(bed, colors));
    root.add(buildEnvelope(env, bed, colors));
    root.add(buildPiece(mesh, bed, colors));
    scene.add(root);

    // Fit the envelope box in view, seen from the front left and above.
    const centre = new THREE.Vector3(bed[0] / 2, bed[1] / 2, env[2] * 0.4);
    const radius = Math.hypot(Math.max(bed[0], env[0]), Math.max(bed[1], env[1]), env[2]) / 2;
    const fit = () => {
      const half = THREE.MathUtils.degToRad(camera.fov / 2);
      const halfH = Math.atan(Math.tan(half) * camera.aspect);
      const dist = (radius / Math.sin(Math.min(half, halfH))) * 1.0;
      const dir = new THREE.Vector3(-0.8, -1.1, 0.85).normalize();
      controls.target.copy(centre);
      camera.position.copy(centre).addScaledVector(dir, dist);
      camera.near = Math.max(0.5, dist / 100);
      camera.far = dist * 20;
      camera.updateProjectionMatrix();
      controls.update();
    };

    let frame = 0;
    const render = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        renderer.render(scene, camera);
      });
    };
    controls.addEventListener('change', render);
    let fitted = false;
    const resize = () => {
      const w = host.clientWidth;
      const h = host.clientHeight;
      if (!w || !h) return;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      if (!fitted) {
        fit();
        fitted = true;
      }
      render();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    resize();

    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
      controls.dispose();
      disposeTree(root);
      // Release the WebGL context now: browsers cap live contexts (about 16).
      renderer.forceContextLoss();
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [mesh]);

  return (
    <div className="model3d piece-preview-3d" ref={hostRef} data-testid="piece-preview-3d" data-webgl={failed ? 'unavailable' : 'ok'}>
      {failed ? (
        <div className="model3d-fallback">
          <p className="small muted">
            This browser cannot show 3D graphics (WebGL is not available). The piece measures {dimsText(mesh.size_mm, 1)} in its print orientation.
          </p>
        </div>
      ) : null}
    </div>
  );
}
