import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `host: true` exposes the dev server outside the container (docker-compose).
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
  },
});
