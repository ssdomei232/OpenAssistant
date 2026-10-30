import { babelTransformPlugin } from './scripts/babel-plugin.mjs';

// 直接导出普通对象，避免在 configLoader=runner 下 import 'vitest/config'。
export default {
  plugins: [babelTransformPlugin()],
  esbuild: false,
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    restoreMocks: true,
    clearMocks: true,
    include: ['src/**/*.test.{ts,tsx}'],
    // 沙箱禁止 fork 子进程（管道 stdio），改用 worker_threads。
    pool: 'threads',
    poolOptions: { threads: { singleThread: true } },
    fileParallelism: false,
  },
};
