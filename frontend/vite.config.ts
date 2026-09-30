import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";
import { defineConfig } from "vite";

const pkg = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf-8")) as { version: string };
const backend = process.env.VITE_BACKEND ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: backend, changeOrigin: false },
      "/ws": { target: backend.replace("http", "ws"), ws: true, changeOrigin: false },
    },
  },
  build: { chunkSizeWarningLimit: 1500 },
});
