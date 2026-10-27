import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run dev` proxies the API to a running `nemo serve`; `npm run build` writes into the Python package.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "../nemo/static/console", emptyOutDir: true },
  server: {
    proxy: {
      "/api": { target: "http://127.0.0.1:7860", changeOrigin: false },
      "/v1": { target: "http://127.0.0.1:7860", changeOrigin: false },
    },
  },
});
