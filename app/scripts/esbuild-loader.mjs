/**
 * Vite 内部仍会 import 'esbuild'（例如 HTML/define 替换）。
 * 该 loader 把 'esbuild' 重定向到纯 JS 的 shim，避免启动 esbuild service 子进程。
 */
const shimUrl = new URL('./esbuild-shim.mjs', import.meta.url).href;

export async function resolve(specifier, context, nextResolve) {
  if (specifier === 'esbuild') {
    return { url: shimUrl, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}
