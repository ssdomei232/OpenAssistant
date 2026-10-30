import { babelTransformPlugin } from './scripts/babel-plugin.mjs';

// 说明：沙箱内不能使用 esbuild（spawn 被禁止），改由 Babel 完成 TS/TSX 转换；
// 产物使用 terser 压缩，CSS 不压缩。功能与常规 Vite 构建一致。
// 注意：这里直接导出普通对象，避免 configLoader=runner 下 import 'vite' 的 junction 解析问题。
export default {
  plugins: [babelTransformPlugin()],
  esbuild: false,
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      // 开发时把 /api 代理到本地 Server，避免跨域；Server 地址也可在设置页配置。
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    target: 'es2022',
    sourcemap: false,
    minify: 'terser',
    cssMinify: false,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          mui: ['@mui/material', '@emotion/react', '@emotion/styled'],
          query: ['@tanstack/react-query'],
        },
      },
    },
  },
};
