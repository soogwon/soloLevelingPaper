import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
    exclude: ["node_modules/**", "dist/**", ".bkit-codex/**", ".agents/**", ".codex/**"],
    coverage: { reporter: ["text", "json-summary"] },
    restoreMocks: true,
  },
});
