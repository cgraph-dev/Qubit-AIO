'use client';

import { useEffect, useRef } from 'react';

/**
 * Cheap Three.js depth field behind the HUD: one grid, three wire rings and a
 * bounded point cloud. DPR is capped, the loop is throttled to ~30 fps, paused
 * while the document is hidden, and never starts for reduced-motion users
 * (they get the static CSS grid/vignette fallback underneath).
 */
export function HudBackground({ reduced }: { reduced: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    if (reduced) return;
    const canvas = canvasRef.current;
    if (!canvas) return;

    let disposed = false;
    let raf = 0;
    let cleanup: (() => void) | undefined;

    void (async () => {
      try {
        const THREE = await import('three');
        if (disposed || !canvasRef.current) return;

        const renderer = new THREE.WebGLRenderer({
          canvas,
          antialias: false,
          alpha: true,
          powerPreference: 'low-power',
        });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
        renderer.setClearColor(0x000000, 0);
        renderer.setSize(window.innerWidth, window.innerHeight, false);

        const scene = new THREE.Scene();
        scene.fog = new THREE.FogExp2(0x05020b, 0.055);

        const camera = new THREE.PerspectiveCamera(
          52,
          window.innerWidth / Math.max(1, window.innerHeight),
          0.1,
          120,
        );
        camera.position.set(0, 1.5, 9.4);
        camera.lookAt(0, 0.2, 0);

        const grid = new THREE.GridHelper(64, 64, 0x9b5cff, 0x2a1c46);
        grid.position.y = -2.5;
        const gridMaterials = Array.isArray(grid.material)
          ? grid.material
          : [grid.material];
        gridMaterials.forEach((material) => {
          material.transparent = true;
          material.opacity = 0.12;
          material.depthWrite = false;
        });
        scene.add(grid);

        const ringGeom = new THREE.TorusGeometry(1.35, 0.004, 3, 140);
        const ringMat = new THREE.MeshBasicMaterial({
          color: 0xff2bd6,
          transparent: true,
          opacity: 0.2,
          depthWrite: false,
        });
        const ring = new THREE.Mesh(ringGeom, ringMat);
        ring.rotation.set(1.16, 0.28, 0);
        ring.position.set(-2.7, 2.1, -2.4);
        scene.add(ring);

        const ringGeom2 = new THREE.TorusGeometry(1.8, 0.003, 3, 140);
        const ringMat2 = new THREE.MeshBasicMaterial({
          color: 0x00e6ff,
          transparent: true,
          opacity: 0.1,
          depthWrite: false,
        });
        const ring2 = new THREE.Mesh(ringGeom2, ringMat2);
        ring2.rotation.set(-1.42, -0.4, 0.2);
        ring2.position.set(3.0, 1.4, -3.2);
        scene.add(ring2);

        const ringGeom3 = new THREE.TorusGeometry(0.95, 0.003, 3, 120);
        const ringMat3 = new THREE.MeshBasicMaterial({
          color: 0x9b5cff,
          transparent: true,
          opacity: 0.14,
          depthWrite: false,
        });
        const ring3 = new THREE.Mesh(ringGeom3, ringMat3);
        ring3.rotation.set(0.7, 0.95, 0.1);
        ring3.position.set(1.9, -1.9, -1.8);
        scene.add(ring3);

        const count = 320;
        const positions = new Float32Array(count * 3);
        for (let i = 0; i < count; i += 1) {
          positions[i * 3] = (Math.random() - 0.5) * 12;
          positions[i * 3 + 1] = (Math.random() - 0.3) * 8;
          positions[i * 3 + 2] = (Math.random() - 0.4) * 6 - 1;
        }
        const pointGeom = new THREE.BufferGeometry();
        pointGeom.setAttribute(
          'position',
          new THREE.BufferAttribute(positions, 3),
        );
        const pointMat = new THREE.PointsMaterial({
          color: 0x00e6ff,
          size: 0.018,
          transparent: true,
          opacity: 0.32,
          depthWrite: false,
          sizeAttenuation: true,
        });
        const points = new THREE.Points(pointGeom, pointMat);
        scene.add(points);

        const pointer = { x: 0, y: 0 };
        const onPointer = (event: PointerEvent) => {
          pointer.x = (event.clientX / window.innerWidth - 0.5) * 2;
          pointer.y = (event.clientY / window.innerHeight - 0.5) * 2;
        };
        window.addEventListener('pointermove', onPointer, { passive: true });

        const resize = () => {
          const width = window.innerWidth;
          const height = Math.max(1, window.innerHeight);
          camera.aspect = width / height;
          camera.updateProjectionMatrix();
          renderer.setSize(width, height, false);
          renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
        };
        window.addEventListener('resize', resize, { passive: true });

        const start = performance.now();
        let last = 0;

        const frame = (now: number) => {
          if (disposed) return;
          raf = requestAnimationFrame(frame);
          if (document.hidden) return;
          if (now - last < 33) return;
          last = now;

          const t = (now - start) / 1000;
          const intro = Math.min(1, t / 1.5);
          const eased = 1 - (1 - intro) ** 3;

          camera.position.z = 11.2 - 1.8 * eased;
          camera.position.x += (pointer.x * 0.22 - camera.position.x) * 0.02;
          camera.position.y +=
            (1.5 + pointer.y * -0.12 - camera.position.y) * 0.02;
          camera.lookAt(0, 0.1, 0);

          ring.rotation.z = t * 0.12;
          ring.rotation.x = 1.16 + Math.sin(t * 0.24) * 0.08;
          ring2.rotation.z = -t * 0.08;
          ring2.rotation.y = -0.4 + Math.cos(t * 0.19) * 0.1;
          ring3.rotation.y = 0.95 + Math.sin(t * 0.31) * 0.12;
          points.rotation.y = t * 0.018;
          points.rotation.x = Math.sin(t * 0.11) * 0.03;
          grid.position.z = ((t * 0.45) % 2) - 1;

          renderer.render(scene, camera);
        };

        raf = requestAnimationFrame(frame);

        cleanup = () => {
          window.removeEventListener('pointermove', onPointer);
          window.removeEventListener('resize', resize);
          ringGeom.dispose();
          ringMat.dispose();
          ringGeom2.dispose();
          ringMat2.dispose();
          ringGeom3.dispose();
          ringMat3.dispose();
          pointGeom.dispose();
          pointMat.dispose();
          grid.geometry.dispose();
          (Array.isArray(grid.material)
            ? grid.material
            : [grid.material]
          ).forEach((material) => material.dispose());
          renderer.dispose();
        };
      } catch {
        // WebGL is unavailable (headless/test/no GPU): the CSS fallback stays.
      }
    })();

    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
      cleanup?.();
    };
  }, [reduced]);

  return (
    <div className="bg" aria-hidden="true">
      <div className="bg__wash" />
      <div className="bg__grid" />
      <canvas ref={canvasRef} className="bg__canvas" />
      <div className="bg__horizon" />
      <div className="bg__vignette" />
    </div>
  );
}
