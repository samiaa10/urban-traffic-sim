import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    // The 3D map's chunk (MapLibre, about 1 MB, 275 kB compressed) is
    // loaded only when someone switches to 3D; the main bundle stays small
    chunkSizeWarningLimit: 1100,
    rollupOptions: {
      output: {
        // MapLibre's worker (src/maplibre.ts) ships as maplibre-gl-worker.mjs.
        // Saved as .js, so every server (nginx, Render) sends it as
        // JavaScript: browsers refuse to run a module worker otherwise, and
        // nginx's default types do not include .mjs.
        assetFileNames: (asset) =>
          asset.names.some((name) => name.endsWith('.mjs'))
            ? 'assets/[name]-[hash].js'
            : 'assets/[name]-[hash][extname]',
      },
    },
  },
})
