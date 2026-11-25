import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Canvas, useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import type * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { ApiError, getModelGlb } from "../lib/api";
import { useReducedMotion, useResolvedTheme } from "../lib/motion";
import { G } from "./glossary";
import { readColors, ViewController, type ViewState } from "./viewer3d";

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

/** Lives inside the canvas: hands the renderer, camera and scene to the controller. */
function Bridge({
  ctrl,
  model,
  lineNodes,
  state,
  theme,
  reduced,
  host,
  onPickLine,
}: {
  ctrl: ViewController;
  model: THREE.Object3D;
  lineNodes: Map<string, string[]>;
  state: ViewState;
  theme: string;
  reduced: boolean;
  host: React.RefObject<HTMLDivElement | null>;
  onPickLine: (lineId: string) => void;
}) {
  const { camera, gl, scene, invalidate } = useThree();

  useEffect(() => {
    ctrl.attach(camera, gl, scene, invalidate, host.current, reduced);
    return () => {
      ctrl.detach();
    };
  }, [ctrl, camera, gl, scene, invalidate, host, reduced]);

  useEffect(() => {
    ctrl.setModel(model, lineNodes);
  }, [ctrl, model, lineNodes]);

  useEffect(() => {
    if (host.current) ctrl.apply(state, readColors(host.current));
  }, [ctrl, state, theme, host, model, lineNodes, reduced]);

  useFrame(() => {
    ctrl.tick();
  });

  return (
    <primitive
      object={model}
      onClick={(e: ThreeEvent<MouseEvent>) => {
        const id = ctrl.lineOf(e.object);
        if (id) {
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
  const [ctrl] = useState(() => new ViewController());
  const [model, setModel] = useState<THREE.Object3D | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "none">("loading");
  const [message, setMessage] = useState("");
  const [isolate, setIsolate] = useState(false);
  const [showExcluded, setShowExcluded] = useState(false);
  const [sectionOn, setSectionOn] = useState(false);
  const [axis, setAxis] = useState<"x" | "y" | "z">("y");
  const [t, setT] = useState(0.5);
  const ids = { axis: useId(), slider: useId() };

  useEffect(
    () => () => {
      ctrl.dispose();
    },
    [ctrl],
  );

  // Load the model with the Authorization header and parse it in memory.
  useEffect(() => {
    let cancelled = false;
    getModelGlb(jobId, token)
      .then(
        (buf) =>
          new Promise<THREE.Object3D>((resolve, reject) => {
            new GLTFLoader().parse(
              buf,
              "",
              (g) => {
                resolve(g.scene);
              },
              reject,
            );
          }),
      )
      .then((s) => {
        if (cancelled) return;
        setModel(s);
        setStatus("ready");
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setStatus("none");
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

  const state = useMemo<ViewState>(
    () => ({
      highlight: highlightNodes,
      excluded: excludedNodes,
      isolate,
      showExcluded,
      section: { on: sectionOn, axis, t },
    }),
    [highlightNodes, excludedNodes, isolate, showExcluded, sectionOn, axis, t],
  );

  const ready = status === "ready" && model !== null;

  return (
    <section className="rv-panel rv-viewer" aria-label="3D model">
      <div className="rv-viewer-bar" role="toolbar" aria-label="3D view controls">
        <button
          type="button"
          className="btn btn-secondary"
          disabled={!ready}
          onClick={() => {
            ctrl.fit(highlightNodes);
          }}
        >
          Fit
        </button>
        <button
          type="button"
          className="btn btn-secondary"
          aria-pressed={isolate}
          disabled={!ready || highlightNodes.length === 0}
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
          disabled={!ready || excludedNodes.length === 0}
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
          disabled={!ready}
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
        // The 3D view has its own keyboard model (arrows pan), so it must be focusable.
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
        tabIndex={0}
        role="application"
        aria-label="3D model view. Drag to rotate, scroll to zoom, arrow keys to pan. Selecting a line in the table highlights its parts here."
      >
        {status === "loading" ? <p className="rv-viewer-note">Loading the 3D model{"…"}</p> : null}
        {status === "none" ? <p className="rv-viewer-note">{message}</p> : null}
        {ready ? (
          <Canvas frameloop="demand" camera={{ position: [3, 2, 3], fov: 40 }} dpr={[1, 2]} aria-hidden="true">
            <ambientLight intensity={1.1} />
            <directionalLight position={[4, 6, 5]} intensity={2.2} />
            <directionalLight position={[-5, 2, -4]} intensity={0.9} />
            <Bridge
              ctrl={ctrl}
              model={model}
              lineNodes={lineNodes}
              state={state}
              theme={theme}
              reduced={reduced}
              host={host}
              onPickLine={onPickLine}
            />
          </Canvas>
        ) : null}
      </div>
    </section>
  );
}
