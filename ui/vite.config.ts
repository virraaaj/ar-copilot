import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // Dev-time proxy so `npm run dev` can hit the FastAPI backend without
      // CORS setup -- production serves both from the same FastAPI process
      // (see app/main.py), where this isn't needed at all.
      '/api': 'http://localhost:8090',
    },
  },
})
