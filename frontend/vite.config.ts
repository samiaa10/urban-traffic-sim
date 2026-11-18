import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    // The 3D map's chunk (MapLibre, about 1 MB, 275 kB compressed) is
    // loaded only when someone switches to 3D; the main bundle stays small
    chunkSizeWarningLimit: 1100,
  },
})
