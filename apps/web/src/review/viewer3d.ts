/**
 * The imperative part of the 3D viewer: three.js objects, materials, clipping and the camera.
 * It is plain TypeScript (no React), so React only decides *what* to show and calls these methods.
 */
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

export interface Colors {
  bg: THREE.Color;
  grid: THREE.Color;
  highlight: THREE.Color;
  dim: THREE.Color;
  part: THREE.Color;
  excluded: THREE.Color;
}

export interface ViewState {
  highlight: string[];
  excluded: string[];
  isolate: boolean;
  showExcluded: boolean;
  section: { on: boolean; axis: "x" | "y" | "z"; t: number };
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

/** Resolves the design tokens (light-dark() pairs) to the colours shown right now. */
export function readColors(host: HTMLElement): Colors {
  const probe = document.createElement("span");
  probe.style.display = "none";
  host.append(probe);
  const out = {} as Colors;
  for (const key of Object.keys(TOKENS) as (keyof Colors)[]) {
    probe.style.color = `var(${TOKENS[key]})`;
    const m = /rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/.exec(getComputedStyle(probe).color);
    out[key] = m
      ? new THREE.Color().setRGB(
          Number(m[1]) / 255,
          Number(m[2]) / 255,
          Number(m[3]) / 255,
          THREE.SRGBColorSpace,
        )
      : new THREE.Color(FALLBACK[key]);
  }
  probe.remove();
  return out;
}

interface MeshInfo {
  mesh: THREE.Mesh;
  material: THREE.MeshStandardMaterial;
  /** Names of the mesh and all its ancestors: the CAD node ids it belongs to. */
  chain: Set<string>;
}

interface Glide {
  t0: number;
  from: THREE.Vector3;
  to: THREE.Vector3;
  fromTarget: THREE.Vector3;
  toTarget: THREE.Vector3;
}

export class ViewController {
  private camera: THREE.PerspectiveCamera | null = null;
  private gl: THREE.WebGLRenderer | null = null;
  private stage: THREE.Scene | null = null;
  private controls: OrbitControls | null = null;
  private grid: THREE.GridHelper | null = null;
  private model: THREE.Object3D | null = null;
  private infos: MeshInfo[] = [];
  private bounds: THREE.Box3 | null = null;
  private glide: Glide | null = null;
  private reduced = false;
  private invalidate: () => void = () => undefined;
  private readonly plane = new THREE.Plane(new THREE.Vector3(0, -1, 0), 0);

  attach(
    camera: THREE.Camera,
    gl: THREE.WebGLRenderer,
    stage: THREE.Scene,
    invalidate: () => void,
    keyTarget: HTMLElement | null,
    reduced: boolean,
  ): void {
    if (!(camera instanceof THREE.PerspectiveCamera)) return;
    this.camera = camera;
    this.gl = gl;
    this.stage = stage;
    this.invalidate = invalidate;
    this.reduced = reduced;
    gl.localClippingEnabled = true;
    const c = new OrbitControls(camera, gl.domElement);
    c.enableDamping = !reduced;
    c.addEventListener("change", () => {
      this.invalidate();
    });
    if (keyTarget) c.listenToKeyEvents(keyTarget);
    this.controls = c;
    if (this.grid) stage.add(this.grid);
  }

  detach(): void {
    this.controls?.dispose();
    this.controls = null;
    if (this.grid) this.stage?.remove(this.grid);
    this.camera = null;
    this.gl = null;
    this.stage = null;
    this.glide = null;
  }

  /** Indexes the meshes: which CAD node (and so which BoM line) each belongs to. */
  setModel(model: THREE.Object3D, lineNodes: Map<string, string[]>): void {
    for (const i of this.infos) i.material.dispose();
    this.model = model;
    const nodeToLine = new Map<string, string>();
    for (const [lineId, nodes] of lineNodes) for (const n of nodes) nodeToLine.set(n, lineId);
    const found: MeshInfo[] = [];
    model.traverse((o) => {
      if (!(o instanceof THREE.Mesh)) return;
      const mesh = o as unknown as THREE.Mesh;
      const chain = new Set<string>();
      let lineId: string | null = null;
      for (let p: THREE.Object3D | null = mesh; p; p = p.parent) {
        // The glTF loader makes repeated names unique ("N-0008" -> "N-0008_1"): instances of one
        // part share a CAD node id, so the suffix is removed to recover it.
        const nodeId = p.name.replace(/_\d+$/, "");
        chain.add(nodeId);
        if (lineId === null) lineId = nodeToLine.get(nodeId) ?? null;
      }
      const material = new THREE.MeshStandardMaterial({
        roughness: 0.55,
        metalness: 0.15,
        side: THREE.DoubleSide,
      });
      mesh.material = material;
      mesh.userData.lineId = lineId;
      found.push({ mesh, material, chain });
    });
    this.infos = found;
    this.bounds = new THREE.Box3().setFromObject(model);
    this.placeGrid();
    this.moveTo(this.bounds);
  }

  private placeGrid(): void {
    if (this.grid) {
      this.stage?.remove(this.grid);
      this.grid.dispose();
    }
    const b = this.bounds;
    if (!b || b.isEmpty()) return;
    const size = Math.max(...b.getSize(new THREE.Vector3()).toArray(), 0.01) * 2.5;
    this.grid = new THREE.GridHelper(size, 20);
    this.grid.position.set((b.min.x + b.max.x) / 2, b.min.y, (b.min.z + b.max.z) / 2);
    this.stage?.add(this.grid);
  }

  /** Colours, dimming, isolation, excluded bodies and the section plane. */
  apply(state: ViewState, colors: Colors): void {
    const sel = new Set(state.highlight);
    const exc = new Set(state.excluded);
    const hasSel = sel.size > 0;
    if (this.gl) this.gl.setClearColor(colors.bg);
    const gridMaterial = this.grid?.material;
    if (gridMaterial && !Array.isArray(gridMaterial)) {
      gridMaterial.color.copy(colors.grid);
    }
    const b = this.bounds;
    const axis = state.section.axis;
    if (b) {
      const lo = b.min[axis];
      const hi = b.max[axis];
      this.plane.normal.set(
        axis === "x" ? -1 : 0,
        axis === "y" ? -1 : 0,
        axis === "z" ? -1 : 0,
      );
      this.plane.constant = hi - state.section.t * (hi - lo);
    }
    for (const i of this.infos) {
      const isSel = hasSel && [...i.chain].some((n) => sel.has(n));
      const isExc = [...i.chain].some((n) => exc.has(n));
      i.mesh.visible = (state.showExcluded || !isExc) && !(state.isolate && hasSel && !isSel);
      const m = i.material;
      m.color.copy(
        isExc ? colors.excluded : isSel ? colors.highlight : hasSel ? colors.dim : colors.part,
      );
      m.transparent = hasSel && !isSel;
      m.opacity = hasSel && !isSel ? 0.5 : 1;
      m.clippingPlanes = state.section.on ? [this.plane] : [];
      m.needsUpdate = true;
    }
    this.invalidate();
  }

  /** Frames the given CAD nodes, or the whole model when none are given. */
  fit(nodeIds: string[]): void {
    let box = this.bounds;
    if (nodeIds.length > 0) {
      const wanted = new Set(nodeIds);
      const sub = new THREE.Box3();
      for (const i of this.infos) {
        if (i.mesh.visible && [...i.chain].some((n) => wanted.has(n))) sub.expandByObject(i.mesh);
      }
      if (!sub.isEmpty()) box = sub;
    }
    if (box) this.moveTo(box);
  }

  /** The line a mesh belongs to (set while indexing), or null. */
  lineOf(object: THREE.Object3D): string | null {
    const id: unknown = object.userData.lineId;
    return typeof id === "string" ? id : null;
  }

  private moveTo(box: THREE.Box3): void {
    const cam = this.camera;
    const c = this.controls;
    if (!cam || !c || box.isEmpty()) return;
    const centre = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3()).length() || 1;
    const dist = (size / 2 / Math.tan((cam.fov * Math.PI) / 360)) * 1.15;
    const to = centre.clone().add(new THREE.Vector3(1, 0.75, 1).normalize().multiplyScalar(dist));
    cam.near = size / 1000;
    cam.far = size * 50;
    cam.updateProjectionMatrix();
    if (this.reduced) {
      cam.position.copy(to);
      c.target.copy(centre);
      c.update();
    } else {
      this.glide = {
        t0: performance.now(),
        from: cam.position.clone(),
        to,
        fromTarget: c.target.clone(),
        toTarget: centre,
      };
    }
    this.invalidate();
  }

  /** Called every frame: advances the glide, then the orbit controls. */
  tick(): void {
    const cam = this.camera;
    const c = this.controls;
    const g = this.glide;
    if (g && cam && c) {
      const t = Math.min(1, (performance.now() - g.t0) / 600);
      const k = 1 - Math.pow(1 - t, 3);
      cam.position.lerpVectors(g.from, g.to, k);
      c.target.lerpVectors(g.fromTarget, g.toTarget, k);
      if (t >= 1) this.glide = null;
      this.invalidate();
    }
    c?.update();
  }

  dispose(): void {
    this.detach();
    for (const i of this.infos) i.material.dispose();
    this.infos = [];
    this.grid?.dispose();
    this.grid = null;
    this.model = null;
  }

  get hasModel(): boolean {
    return this.model !== null;
  }
}
