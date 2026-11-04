import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The UI is served by the Strategy Lab API from web/dist; `npm run dev` proxies /api to a running API.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: { port: 5178, proxy: { "/api": process.env.SL_API ?? "http://127.0.0.1:8070" } },
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 1200 },
  test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"] },
});
