import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Canvas, useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { ApiError, getModelGlb } from "../lib/api";
import { useReducedMotion, useResolvedTheme } from "../lib/motion";
import { G } from "./glossary";

export interface ViewerProps {
  jobId: string;
  token: string;
  /** CAD node ids per BoM line (line.cad_nodes). */
  lineNodes: Map<string, string[]>;
  /** The node ids to highlight (the selected line's, or excluded bodies picked in the table). */
  highlightNodes: string[];
  excludedNodes: string[];
  onPickLine: (lineId: string) => void;
}

interface Colors {
  bg: THREE.Color;
  grid: THREE.Color;
  highlight: THREE.Color;
  dim: THREE.Color;
  part: THREE.Color;
  excluded: THREE.Color;
}

const FALLBACK: Record<keyof Colors, string> = {
  bg: "#e9edf2",
  grid: "#c3cbd6",
  highlight: "#1f5fd1",
  dim: "#b9c1cc",
  part: "#8a96a8",
  excluded: "#d9a441",
};
const TOKENS: Record<keyof Colors, string> = {
  bg: "--c-viewer-bg",
  grid: "--c-viewer-grid",
  highlight: "--c-viewer-highlight",
  dim: "--c-viewer-dim",
  part: "--c-viewer-part",
  excluded: "--c-viewer-excluded",
};

/** Resolves a design token (which is a light-dark() pair) to the colour shown right now. */
function readColors(host: HTMLElement): Colors {
  const probe = document.createElement("span");
  probe.style.display = "none";
  host.append(probe);
  const out = {} as Colors;
  for (const key of Object.keys(TOKENS) as (keyof Colors)[]) {
    probe.style.color = `var(${TOKENS[key]})`;
    const m = /rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/.exec(getComputedStyle(probe).color);
    out[key] = m
      ? new THREE.Color().setRGB(Number(m[1]) / 255, Number(m[2]) / 255, Number(m[3]) / 255, THREE.SRGBColorSpace)
      : new THREE.Color(FALLBACK[key]);
  }
  probe.remove();
  return out;
}

interface MeshInfo {
  mesh: THREE.Mesh;
  material: THREE.MeshStandardMaterial;
  /** Names of this mesh and every ancestor: the CAD node ids it belongs to. */
  chain: Set<string>;
  lineId: string | null;
}

interface Camera {
  fit: number;
  box: THREE.Box3 | null;
}

function Controls({ reduced, fit, target }: { reduced: boolean; fit: Camera; target: React.RefObject<HTMLElement | null> }) {
  const { camera, gl, invalidate } = useThree();
  const controls = useRef<OrbitControls | null>(null);
  const anim = useRef<{
    t0: number;
    from: THREE.Vector3;
    to: THREE.Vector3;
    fromT: THREE.Vector3;
    toT: THREE.Vector3;
  } | null>(null);

  useEffect(() => {
    const c = new OrbitControls(camera, gl.domElement);
    c.enableDamping = !reduced;
    c.addEventListener("change", invalidate);
    if (target.current) c.listenToKeyEvents(target.current);
    controls.current = c;
    return () => {
      c.removeEventListener("change", invalidate);
      c.dispose();
      controls.current = null;
    };
  }, [camera, gl, invalidate, reduced, target]);

  // "Fit" (and the first load) frames the box: glide there, or jump when motion is reduced.
  useEffect(() => {
    const c = controls.current;
    const box = fit.box;
    if (!c || !box || box.isEmpty()) return;
    const centre = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3()).length() || 1;
    const persp = camera as THREE.PerspectiveCamera;
    const dist = (size / 2 / Math.tan((persp.fov * Math.PI) / 360)) * 1.15;
    const dir = new THREE.Vector3(1, 0.75, 1).normalize();
    const to = centre.clone().add(dir.multiplyScalar(dist));
    persp.near = size / 1000;
    persp.far = size * 50;
    persp.updateProjectionMatrix();
    if (reduced) {
      camera.position.copy(to);
      c.target.copy(centre);
      c.update();
      invalidate();
    } else {
      anim.current = {
        t0: performance.now(),
        from: camera.position.clone(),
        to,
        fromT: c.target.clone(),
        toT: centre,
      };
      invalidate();
    }
  }, [fit, camera, reduced, invalidate]);

  useFrame(() => {
    const c = controls.current;
    const a = anim.current;
    if (a && c) {
      const t = Math.min(1, (performance.now() - a.t0) / 600);
      const k = 1 - Math.pow(1 - t, 3);
      camera.position.lerpVectors(a.from, a.to, k);
      c.target.lerpVectors(a.fromT, a.toT, k);
      if (t >= 1) anim.current = null;
      invalidate();
    }
    c?.update();
  });
  return null;
}

function Scene({
  scene,
  lineNodes,
  highlightNodes,
  excludedNodes,
  colors,
  isolate,
  showExcluded,
  section,
  onPickLine,
  onBounds,
}: {
  scene: THREE.Object3D;
  lineNodes: Map<string, string[]>;
  highlightNodes: string[];
  excludedNodes: string[];
  colors: Colors;
  isolate: boolean;
  showExcluded: boolean;
  section: { on: boolean; axis: "x" | "y" | "z"; t: number };
  onPickLine: (lineId: string) => void;
  onBounds: (box: THREE.Box3) => void;
}) {
  const { gl, invalidate } = useThree();
  const [infos, setInfos] = useState<MeshInfo[]>([]);
  const plane = useMemo(() => new THREE.Plane(new THREE.Vector3(0, -1, 0), 0), []);

  // Index the meshes: which CAD node (and so which BoM line) each one belongs to.
  useEffect(() => {
    const nodeToLine = new Map<string, string>();
    for (const [lineId, nodes] of lineNodes) for (const n of nodes) nodeToLine.set(n, lineId);
    const found: MeshInfo[] = [];
    scene.traverse((o) => {
      if (!(o instanceof THREE.Mesh)) return;
      const chain = new Set<string>();
      let lineId: string | null = null;
      for (let p: THREE.Object3D | null = o; p; p = p.parent) {
        chain.add(p.name);
        if (lineId === null && nodeToLine.has(p.name)) lineId = nodeToLine.get(p.name) ?? null;
      }
      const material = new THREE.MeshStandardMaterial({
        roughness: 0.55,
        metalness: 0.15,
        side: THREE.DoubleSide,
      });
      o.material = material;
      o.userData.lineId = lineId;
      found.push({ mesh: o, material, chain, lineId });
    });
    setInfos(found);
    const box = new THREE.Box3().setFromObject(scene);
    onBounds(box);
    return () => {
      for (const i of found) i.material.dispose();
    };
  }, [scene, lineNodes, onBounds]);

  // Colours, dimming, isolation, excluded bodies and the section plane.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/immutability
    gl.localClippingEnabled = true;
    const sel = new Set(highlightNodes);
    const exc = new Set(excludedNodes);
    const hasSel = sel.size > 0;
    const box = new THREE.Box3().setFromObject(scene);
    const axis = section.axis;
    const lo = box.min[axis];
    const hi = box.max[axis];
    plane.normal.set(axis === "x" ? -1 : 0, axis === "y" ? -1 : 0, axis === "z" ? -1 : 0);
    plane.constant = hi - section.t * (hi - lo);
    for (const i of infos) {
      const isSel = hasSel && [...i.chain].some((n) => sel.has(n));
      const isExc = [...i.chain].some((n) => exc.has(n));
      i.mesh.visible = (showExcluded || !isExc) && !(isolate && hasSel && !isSel);
      const m = i.material;
      m.color.copy(isExc ? colors.excluded : isSel ? colors.highlight : hasSel ? colors.dim : colors.part);
      m.transparent = hasSel && !isSel;
      m.opacity = hasSel && !isSel ? 0.5 : 1;
      m.clippingPlanes = section.on ? [plane] : [];
      m.needsUpdate = true;
    }
    invalidate();
  }, [infos, highlightNodes, excludedNodes, colors, isolate, showExcluded, section, plane, scene, gl, invalidate]);

  return (
    <primitive
      object={scene}
      onClick={(e: ThreeEvent<MouseEvent>) => {
        const id: unknown = e.object.userData.lineId;
        if (typeof id === "string") {
          e.stopPropagation();
          onPickLine(id);
        }
      }}
    />
  );
}

export function Viewer({ jobId, token, lineNodes, highlightNodes, excludedNodes, onPickLine }: ViewerProps) {
  const reduced = useReducedMotion();
  const theme = useResolvedTheme();
  const host = useRef<HTMLDivElement>(null);
  const [scene, setScene] = useState<THREE.Object3D | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "none">("loading");
  const [message, setMessage] = useState("");
  const [colors, setColors] = useState<Colors | null>(null);
  const [isolate, setIsolate] = useState(false);
  const [showExcluded, setShowExcluded] = useState(false);
  const [sectionOn, setSectionOn] = useState(false);
  const [axis, setAxis] = useState<"x" | "y" | "z">("y");
  const [t, setT] = useState(0.5);
  const [fit, setFit] = useState<Camera>({ fit: 0, box: null });
  const bounds = useRef<THREE.Box3 | null>(null);
  const ids = { axis: useId(), slider: useId() };

  // Load the model with the Authorization header and parse it in memory.
  useEffect(() => {
    let cancelled = false;
    getModelGlb(jobId, token)
      .then(
        (buf) =>
          new Promise<THREE.Object3D>((resolve, reject) => {
            new GLTFLoader().parse(buf, "", (g) => {
              resolve(g.scene);
            }, reject);
          }),
      )
      .then((s) => {
        if (cancelled) return;
        setScene(s);
        setState("ready");
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setState("none");
        setMessage(
          e instanceof ApiError && e.status === 404
            ? "There is no 3D model for this job. Jobs built only from a BoM file have none."
            : "The 3D model could not be loaded. The BoM table still works.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [jobId, token]);

  // Viewer colours follow the design tokens, in both themes.
  useEffect(() => {
    if (host.current) setColors(readColors(host.current));
  }, [theme, state]);

  const onBounds = useRef((box: THREE.Box3) => {
    bounds.current = box;
    setFit((f) => ({ fit: f.fit + 1, box }));
  }).current;

  function fitView() {
    // Frame the selection when there is one, otherwise the whole model.
    let box = bounds.current;
    if (scene && highlightNodes.length > 0) {
      const sel = new Set(highlightNodes);
      const sub = new THREE.Box3();
      scene.traverse((o) => {
        if (o instanceof THREE.Mesh && o.visible) {
          for (let p: THREE.Object3D | null = o; p; p = p.parent) {
            if (sel.has(p.name)) {
              sub.expandByObject(o);
              break;
            }
          }
        }
      });
      if (!sub.isEmpty()) box = sub;
    }
    if (box) setFit((f) => ({ fit: f.fit + 1, box }));
  }

  const section = useMemo(() => ({ on: sectionOn, axis, t }), [sectionOn, axis, t]);
  const grid = colors?.grid.getStyle() ?? FALLBACK.grid;

  return (
    <section className="rv-panel rv-viewer" aria-label="3D model">
      <div className="rv-viewer-bar" role="toolbar" aria-label="3D view controls">
        <button type="button" className="btn btn-secondary" onClick={fitView} disabled={state !== "ready"}>
          Fit
        </button>
        <button
          type="button"
          className="btn btn-secondary"
          aria-pressed={isolate}
          disabled={state !== "ready" || highlightNodes.length === 0}
          onClick={() => {
            setIsolate((v) => !v);
          }}
        >
          <G term="isolate">Isolate</G>
        </button>
        <button
          type="button"
          className="btn btn-secondary"
          aria-pressed={showExcluded}
          disabled={state !== "ready" || excludedNodes.length === 0}
          onClick={() => {
            setShowExcluded((v) => !v);
          }}
        >
          Show excluded
        </button>
        <button
          type="button"
          className="btn btn-secondary"
          aria-pressed={sectionOn}
          disabled={state !== "ready"}
          onClick={() => {
            setSectionOn((v) => !v);
          }}
        >
          <G term="section plane">Section</G>
        </button>
        {sectionOn ? (
          <span className="rv-section">
            <label htmlFor={ids.axis} className="visually-hidden">
              Section axis
            </label>
            <select
              id={ids.axis}
              value={axis}
              onChange={(e) => {
                setAxis(e.target.value as "x" | "y" | "z");
              }}
            >
              <option value="x">Across X</option>
              <option value="y">Across Y (height)</option>
              <option value="z">Across Z</option>
            </select>
            <label htmlFor={ids.slider} className="visually-hidden">
              Section position
            </label>
            <input
              id={ids.slider}
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={t}
              onChange={(e) => {
                setT(Number(e.target.value));
              }}
            />
          </span>
        ) : null}
      </div>

      <div
        ref={host}
        className="rv-canvas"
        tabIndex={0}
        role="group"
        aria-label="3D model view. Drag to rotate, scroll to zoom, arrow keys to pan. Selecting a line in the table highlights its parts here."
      >
        {state === "loading" ? <p className="rv-viewer-note">Loading the 3D model{"…"}</p> : null}
        {state === "none" ? <p className="rv-viewer-note">{message}</p> : null}
        {state === "ready" && scene && colors ? (
          <Canvas
            frameloop="demand"
            camera={{ position: [3, 2, 3], fov: 40 }}
            dpr={[1, 2]}
            aria-hidden="true"
          >
            <color attach="background" args={[colors.bg]} />
            <ambientLight intensity={1.1} />
            <directionalLight position={[4, 6, 5]} intensity={2.2} />
            <directionalLight position={[-5, 2, -4]} intensity={0.9} />
            <gridHelper args={[10, 20, grid, grid]} />
            <Scene
              scene={scene}
              lineNodes={lineNodes}
              highlightNodes={highlightNodes}
              excludedNodes={excludedNodes}
              colors={colors}
              isolate={isolate}
              showExcluded={showExcluded}
              section={section}
              onPickLine={onPickLine}
              onBounds={onBounds}
            />
            <Controls reduced={reduced} fit={fit} target={host} />
          </Canvas>
        ) : null}
      </div>
    </section>
  );
}
