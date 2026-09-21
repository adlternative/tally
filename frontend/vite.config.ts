import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    proxy: Object.fromEntries(
      ["/sources", "/sourcedata", "/fetch", "/ask", "/token"].map((path) => [
        path,
        "http://127.0.0.1:8020",
      ]),
    ),
  },
});
