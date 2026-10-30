/**
 * 用 Babel 在 Vite 内完成 TS/TSX 转换（纯 JS，无原生依赖、无子进程）。
 *
 * 沙箱环境禁止 Node 以管道 stdio 启动子进程，esbuild 的 service 进程无法启动；
 * @swc/core 的原生 binding 又需要写入工作区外的缓存目录。因此这里使用 Babel：
 *   - @babel/preset-typescript 去掉类型
 *   - @babel/preset-react（automatic runtime）转换 JSX
 * 功能等价于常规 Vite + React 构建，代价是开发时没有 React Fast Refresh（整页刷新）。
 */
import { transformAsync } from '@babel/core';
import presetTypeScript from '@babel/preset-typescript';
import presetReact from '@babel/preset-react';

const TRANSFORM_RE = /\.(ts|tsx|jsx)$/;

export function babelTransformPlugin() {
  return {
    name: 'openassistant:babel-transform',
    enforce: 'pre',
    async transform(code, id) {
      const cleanId = id.split('?')[0];
      if (!TRANSFORM_RE.test(cleanId)) return null;
      if (cleanId.includes('/node_modules/') || cleanId.includes('\\node_modules\\')) return null;
      if (cleanId.includes('/public/') || cleanId.includes('\\public\\')) return null;

      const isTsx = cleanId.endsWith('.tsx') || cleanId.endsWith('.jsx');
      const result = await transformAsync(code, {
        filename: cleanId,
        babelrc: false,
        configFile: false,
        sourceMaps: true,
        presets: [
          [presetTypeScript, { isTSX: isTsx, allExtensions: true, onlyRemoveTypeImports: true }],
          [presetReact, { runtime: 'automatic', development: false }],
        ],
      });
      if (!result?.code) return null;
      return { code: result.code, map: result.map ?? null };
    },
  };
}
