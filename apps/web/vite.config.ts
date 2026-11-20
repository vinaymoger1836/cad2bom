import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In production Caddy serves this build and forwards /api/* to the API (prefix stripped).
// In development Vite does the same forwarding, so the web code always calls /api/...
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env["C2B_API_URL"] ?? "http://localhost:8000",
        changeOrigin: false,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
