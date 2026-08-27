import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Built UI is served by the Python process — one process in real use.
  build: { outDir: "../server/god/static", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8787", changeOrigin: true, ws: false } },
  },
});
