import react from "@vitejs/plugin-react";
import path from "path";
import { defineConfig } from "vite";
import electron from "vite-plugin-electron";
import renderer from "vite-plugin-electron-renderer";

export default defineConfig({
  plugins: [
    react(),
    electron([
      {
        entry: "src/main/index.ts",
        vite: {
          build: {
            outDir: "dist/main",
          },
        },
      },
      {
        entry: "src/preload/index.ts",
        onstart(args) {
          args.reload();
        },
        vite: {
          build: {
            outDir: "dist/preload",
          },
        },
      },
    ]),
    renderer(),
  ],
  resolve: {
    alias: {
      "@shared": path.resolve(__dirname, "src/shared"),
      "@renderer": path.resolve(__dirname, "src/renderer"),
    },
  },
  build: {
    outDir: "dist/renderer",
  },
  // Vite needs to know where the renderer HTML is.
  root: ".",
});
