/**
 * 受限环境下的 Vitest 启动包装，用法与 `vitest` 一致：
 *     node scripts/sandbox-vitest.mjs --run
 */
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

await import('./preload.mjs');

const entry = fs.realpathSync.native(path.resolve('node_modules/vitest/vitest.mjs'));
const args = process.argv.slice(2).filter((arg) => arg !== '--configLoader');
process.argv = [process.argv[0], entry, '--configLoader', 'runner', ...args];
await import(pathToFileURL(entry).href);
