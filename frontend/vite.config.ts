import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发时 /api 代理到后端容器（compose 里服务名为 backend:8000）
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
  },
});
