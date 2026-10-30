/**
 * 纯 JS esbuild shim：仅在受限环境（无法 spawn 子进程）中由 loader 注入。
 *
 * Vite 构建时只会用 esbuild 做 define 字面量替换（import.meta.env.* 等），
 * 这里直接实现；开发服务器的依赖预构建需要真正的打包器，受限环境下会被跳过
 * （用 `pnpm build && pnpm preview` 验证生产行为）。
 */
export const version = '0.28.2';

function replaceDefines(code, define) {
  let output = code;
  const keys = Object.keys(define ?? {}).sort((a, b) => b.length - a.length);
  for (const key of keys) {
    const value = String(define[key]);
    const pattern = new RegExp(`${key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![\\w$])`, 'g');
    output = output.replace(pattern, value);
  }
  return output;
}

export async function transform(code, options = {}) {
  return {
    code: options.define ? replaceDefines(String(code), options.define) : String(code),
    map: '',
    warnings: [],
  };
}

export function transformSync(code, options = {}) {
  return {
    code: options.define ? replaceDefines(String(code), options.define) : String(code),
    map: '',
    warnings: [],
  };
}

export async function formatMessages() {
  return [];
}

export function formatMessagesSync() {
  return [];
}

export function context() {
  throw new Error('受限沙箱中无法运行 esbuild 依赖预构建，请使用 build + preview');
}

export function build() {
  throw new Error('受限沙箱中无法运行 esbuild build');
}

export function buildSync() {
  throw new Error('受限沙箱中无法运行 esbuild buildSync');
}

export default {
  version,
  transform,
  transformSync,
  formatMessages,
  formatMessagesSync,
  context,
  build,
  buildSync,
};
