import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // maplibre-gl bundles its rendering work into a separate worker file
  // that Vite's dependency pre-bundler doesn't resolve correctly by
  // default (observed: requests for maplibre-gl-worker.mjs hang, leaving
  // the map canvas blank with no console error) -- excluding it from
  // pre-bundling is the documented workaround.
  optimizeDeps: {
    exclude: ['maplibre-gl'],
  },
})
