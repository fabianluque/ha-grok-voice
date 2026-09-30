import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { Plugin } from "vite";

const root = dirname(fileURLToPath(import.meta.url));

export const clientRoot = root;
export const wwwRoot = resolve(root, "../grok_voice_agent/www");

export function copyLibToWww(fileName: string): Plugin {
  return {
    name: "copy-lib-to-www",
    closeBundle() {
      const from = resolve(root, "dist", fileName);
      if (!existsSync(from)) {
        throw new Error(`kiosk build did not write ${from}`);
      }
      mkdirSync(wwwRoot, { recursive: true });
      copyFileSync(from, resolve(wwwRoot, fileName));
    },
  };
}
