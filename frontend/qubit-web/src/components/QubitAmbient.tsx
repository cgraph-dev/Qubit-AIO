/**
 * Qubit ambient backdrop — a live three.js scene sitting behind the whole
 * dashboard (every route except the embedded terminal pane, which paints its
 * own opaque surface on top).
 *
 * Kept deliberately cheap: one wireframe icosahedron, one point cloud, one
 * grid, DPR capped, and the render loop pauses while the tab is hidden.
 * Motion is disabled under `prefers-reduced-motion`. If WebGL is unavailable
 * the boundary renders nothing, so the shell keeps working unchanged.
 */
import { Component, memo, type ReactNode, useMemo, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import * as THREE from "three";

const PHOSPHOR = "#7fd4ff";
const HAIRLINE = "#3a5a86";
const DEEP = "#1c2c44";

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** Deterministic PRNG so the lattice is identical on every load (no jitter
 *  between route changes, and no hydration-style mismatch). */
function seeded(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 0x100000000;
  };
}

/** Soft round sprite so points render as glowing dots, never squares.
 *  Generated once on a tiny canvas — no image asset, no network. */
function makeGlowTexture(): THREE.Texture {
  const size = 64;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    const grad = ctx.createRadialGradient(
      size / 2, size / 2, 0,
      size / 2, size / 2, size / 2,
    );
    grad.addColorStop(0, "rgba(255,255,255,1)");
    grad.addColorStop(0.28, "rgba(255,255,255,0.85)");
    grad.addColorStop(0.55, "rgba(255,255,255,0.22)");
    grad.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, size, size);
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.needsUpdate = true;
  return tex;
}

let _glowTexture: THREE.Texture | null = null;
function glowTexture(): THREE.Texture {
  if (!_glowTexture) _glowTexture = makeGlowTexture();
  return _glowTexture;
}

function scatterPositions(
  count: number,
  rand: () => number,
  radiusMin: number,
  radiusSpan: number,
  flattenY: number,
): Float32Array {
  const positions = new Float32Array(count * 3);
  for (let i = 0; i < count; i += 1) {
    const theta = rand() * Math.PI * 2;
    const phi = Math.acos(2 * rand() - 1);
    const r = radiusMin + rand() * radiusSpan;
    positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta) * flattenY;
    positions[i * 3 + 2] = r * Math.cos(phi);
  }
  return positions;
}

/** Near dust — large, dim, slow. Sells depth without shouting. */
function DustField({ count = 420 }: { count?: number }) {
  const geometry = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute(
      "position",
      new THREE.BufferAttribute(
        scatterPositions(count, seeded(20261029), 3.4, 4.6, 0.62),
        3,
      ),
    );
    return geo;
  }, [count]);

  const ref = useRef<THREE.Points>(null);
  useFrame((state, delta) => {
    if (!ref.current) return;
    ref.current.rotation.y += delta * 0.012;
    // Barely-there vertical breathing.
    ref.current.position.y = Math.sin(state.clock.elapsedTime * 0.18) * 0.08;
  });

  return (
    <points ref={ref} geometry={geometry}>
      <pointsMaterial
        color={PHOSPHOR}
        map={glowTexture()}
        size={0.09}
        sizeAttenuation
        transparent
        opacity={0.32}
        alphaTest={0.01}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </points>
  );
}

/** Far sparks — small, bright, twinkling against the dark. */
function SparkField({ count = 260 }: { count?: number }) {
  const geometry = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute(
      "position",
      new THREE.BufferAttribute(
        scatterPositions(count, seeded(707071), 5.2, 3.2, 0.5),
        3,
      ),
    );
    return geo;
  }, [count]);

  const ref = useRef<THREE.Points>(null);
  const mat = useRef<THREE.PointsMaterial>(null);
  useFrame((state, delta) => {
    if (ref.current) ref.current.rotation.y -= delta * 0.02;
    // Gentle twinkle: the whole layer breathes instead of per-point cost.
    if (mat.current) {
      mat.current.opacity =
        0.55 + Math.sin(state.clock.elapsedTime * 1.4) * 0.18;
    }
  });

  return (
    <points ref={ref} geometry={geometry}>
      <pointsMaterial
        ref={mat}
        color="#cfeaff"
        map={glowTexture()}
        size={0.05}
        sizeAttenuation
        transparent
        opacity={0.6}
        alphaTest={0.01}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </points>
  );
}

function Core() {
  const outer = useRef<THREE.Group>(null);
  const inner = useRef<THREE.Mesh>(null);
  const drag = useRef({ x: 0, y: 0 });

  useFrame((state, delta) => {
    // Pointer parallax, damped so the scene never snaps.
    const px = state.pointer.x * 0.35;
    const py = state.pointer.y * 0.22;
    drag.current.x += (px - drag.current.x) * Math.min(1, delta * 2.2);
    drag.current.y += (py - drag.current.y) * Math.min(1, delta * 2.2);
    if (outer.current) {
      outer.current.rotation.y += delta * 0.06;
      outer.current.rotation.x = -drag.current.y;
      outer.current.position.x = drag.current.x * 0.6;
    }
    if (inner.current) {
      inner.current.rotation.y -= delta * 0.11;
      inner.current.rotation.z += delta * 0.03;
    }
  });

  return (
    <group ref={outer}>
      <mesh ref={inner}>
        <icosahedronGeometry args={[2.35, 1]} />
        <meshBasicMaterial
          color={HAIRLINE}
          wireframe
          transparent
          opacity={0.32}
          depthWrite={false}
        />
      </mesh>
      <mesh>
        <icosahedronGeometry args={[1.35, 0]} />
        <meshBasicMaterial
          color={DEEP}
          wireframe
          transparent
          opacity={0.45}
          depthWrite={false}
        />
      </mesh>
    </group>
  );
}

function Floor() {
  const ref = useRef<THREE.Mesh>(null);
  const tilt = useRef<THREE.Mesh>(null);
  useFrame((state, delta) => {
    if (ref.current) ref.current.rotation.z += delta * 0.012;
    if (tilt.current) {
      tilt.current.rotation.z -= delta * 0.008;
      const s = 1 + Math.sin(state.clock.elapsedTime * 0.5) * 0.012;
      tilt.current.scale.set(s, s, 1);
    }
  });
  return (
    <group>
      <mesh ref={ref} rotation={[-Math.PI / 2.35, 0, 0]} position={[0, -2.6, 0]}>
        <ringGeometry args={[3.1, 3.14, 96]} />
        <meshBasicMaterial
          color={PHOSPHOR}
          transparent
          opacity={0.14}
          side={THREE.DoubleSide}
          depthWrite={false}
        />
      </mesh>
      {/* Second tilted halo — catches the eye without adding geometry cost. */}
      <mesh
        ref={tilt}
        rotation={[-Math.PI / 1.9, 0.35, 0.5]}
        position={[0, -1.4, -1.2]}
      >
        <ringGeometry args={[4.3, 4.32, 128]} />
        <meshBasicMaterial
          color={HAIRLINE}
          transparent
          opacity={0.22}
          side={THREE.DoubleSide}
          depthWrite={false}
        />
      </mesh>
    </group>
  );
}

/** Warm halo behind the core — one additive sprite, zero geometry. */
function CoreGlow() {
  const ref = useRef<THREE.Sprite>(null);
  useFrame((state) => {
    if (!ref.current) return;
    const s = 6.4 + Math.sin(state.clock.elapsedTime * 0.7) * 0.35;
    ref.current.scale.set(s, s, 1);
  });
  return (
    <sprite ref={ref} position={[0, 0, -1.5]}>
      <spriteMaterial
        map={glowTexture()}
        color="#2b6ea8"
        transparent
        opacity={0.5}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </sprite>
  );
}

export class AmbientBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}

function QubitAmbientInner() {
  const reduced = prefersReducedMotion();
  return (
    <Canvas
      aria-hidden
      dpr={[1, 1.5]}
      frameloop={reduced ? "demand" : "always"}
      camera={{ position: [0, 0, 6.4], fov: 46 }}
      gl={{ alpha: true, antialias: false, powerPreference: "low-power" }}
      style={{ position: "absolute", inset: 0, pointerEvents: "none" }}
    >
      <CoreGlow />
      <Core />
      <DustField />
      <SparkField />
      <Floor />
    </Canvas>
  );
}

export const QubitAmbient = memo(function QubitAmbient() {
  return (
    <AmbientBoundary>
      <QubitAmbientInner />
    </AmbientBoundary>
  );
});
