import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "src/context_library_manager/static",
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      input: "frontend/main.tsx"
    }
  },
  test: {
    include: ["frontend/**/*.test.tsx"],
    environment: "jsdom",
    setupFiles: ["frontend/test-setup.ts"]
  }
});
