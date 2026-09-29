import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const root = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  base: "./",
  root,
  build: {
    outDir: resolve(root, "../grok_voice_agent/www"),
    emptyOutDir: true,
    rollupOptions: {
      input: resolve(root, "index.html"),
      output: {
        entryFileNames: "ui.js",
        chunkFileNames: "chunks/[name].js",
        assetFileNames: "[name][extname]",
      },
    },
  },
});
