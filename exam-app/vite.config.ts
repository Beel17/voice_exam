import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built into the Python package and served by the service at /exam/. Its paths are
// relative, so the same files also work bundled inside a secure browser (with the
// engine's address in engine-config.js). In development, `npm run dev` proxies the service
// (started separately on 8765).
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: {
    outDir: "../src/voice_agent/exam",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/v1": { target: "http://127.0.0.1:8765", ws: true },
    },
  },
});
