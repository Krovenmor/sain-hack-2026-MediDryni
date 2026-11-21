import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "./",
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000", "/demo": "http://localhost:8000" } },
  build: {
    rolldownOptions: {
      input: "room.js",
      preserveEntrySignatures: "strict",
      output: { entryFileNames: "room.js" },
    },
  },
});
