import { resolve } from "node:path";
import { defineConfig } from "vite";
import { clientRoot, copyLibToWww } from "./vite.www";

export default defineConfig({
  plugins: [copyLibToWww("kiosk-boot.js")],
  build: {
    emptyOutDir: false,
    lib: {
      entry: resolve(clientRoot, "src/boot.ts"),
      name: "GrokVoiceBoot",
      formats: ["iife"],
      fileName: () => "kiosk-boot.js",
    },
  },
});
