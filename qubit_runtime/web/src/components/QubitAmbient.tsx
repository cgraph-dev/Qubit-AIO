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

function PointField({ count = 900 }: { count?: number }) {
  const geometry = useMemo(() => {
    const rand = seeded(20261029);
    const positions = new Float32Array(count * 3);
    for (let i = 0; i < count; i += 1) {
      // Spherical shell, biased outward so the centre stays clear behind text.
      const theta = rand() * Math.PI * 2;
      const phi = Math.acos(2 * rand() - 1);
      const r = 4.2 + rand() * 3.4;
      positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta) * 0.55;
      positions[i * 3 + 2] = r * Math.cos(phi);
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    return geo;
  }, [count]);

  const ref = useRef<THREE.Points>(null);
  useFrame((_, delta) => {
    if (ref.current) ref.current.rotation.y += delta * 0.018;
  });

  return (
    <points ref={ref} geometry={geometry}>
      <pointsMaterial
        color={PHOSPHOR}
        size={0.035}
        sizeAttenuation
        transparent
        opacity={0.5}
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
  useFrame((_, delta) => {
    if (ref.current) ref.current.rotation.z += delta * 0.012;
  });
  return (
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
  );
}

class AmbientBoundary extends Component<
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
      <Core />
      <PointField />
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
