/**
 * 受限环境（如禁止 Node 用管道 stdio 启动子进程的 CI/沙箱）下的 Vite 启动包装。
 *
 * 常规开发/构建请使用 `pnpm dev` / `pnpm build`（直接调用标准 Vite）。
 * 仅在 `vite build` 因 `spawn EPERM` 失败时使用：
 *     node scripts/sandbox-vite.mjs build
 */
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

// preload 必须在 import vite 之前执行：它屏蔽 Vite 在 Windows 上探测网络驱动器
// 时执行的 `net use` 子进程，以及一次性的 realpath 探测。
await import('./preload.mjs');

const entry = fs.realpathSync.native(path.resolve('node_modules/vite/bin/vite.js'));
const args = process.argv.slice(2);
process.argv = [process.argv[0], entry, ...args, '--configLoader', 'runner'];
await import(pathToFileURL(entry).href);
