import { fileURLToPath } from 'node:url'

import { defineConfig } from 'vitest/config'

export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    // Node, not jsdom: everything worth unit-testing here is pure. The parts
    // that need a browser — the GL context, video elements, the frame loop —
    // are verified by driving the real page, because a jsdom stub of WebGL
    // would only ever confirm that the stub behaves like the stub.
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})
