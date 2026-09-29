import { defineConfig } from "vite";

export default defineConfig({
  build: {
    lib: {
      entry: "src/index.ts",
      name: "GrokVoice",
      formats: ["iife"],
      fileName: () => "grok-voice.js",
    },
  },
});
