import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";
import path from "path";
export default defineConfig({
  base: "./",
  plugins: [react(), viteSingleFile()],
  resolve: { alias: { "@": path.resolve(process.cwd(), "src") } },
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 4000, assetsInlineLimit: 100000000 },
});
