/**
 * Static export: `next build` writes the whole HUD to `out/`, which the Qubit
 * bridge can serve as plain files. No Node server, no image optimizer.
 *
 * Optional subpath deployment: set NEXT_PUBLIC_BASE_PATH at build time
 * (e.g. `/voice-edit-hud`) and the exported asset URLs are prefixed.
 */
import process from 'node:process';

const basePath = process.env.NEXT_PUBLIC_BASE_PATH || '/voice-edit';

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export',
  trailingSlash: true,
  reactStrictMode: true,
  images: { unoptimized: true },
  basePath: basePath || undefined,
  assetPrefix: basePath || undefined,
  eslint: { ignoreDuringBuilds: true },
};

export default nextConfig;
