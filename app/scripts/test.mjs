/**
 * Vitest 启动包装：与 vite.mjs 同理，额外参数（如 --run）原样透传。
 */
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import './preload.mjs';

const entry = fs.realpathSync.native(path.resolve('node_modules/vitest/vitest.mjs'));
const args = process.argv
  .slice(2)
  .filter((arg) => arg !== '--' && arg !== '--configLoader');
process.argv = [process.argv[0], entry, '--configLoader', 'native', ...args];
await import(pathToFileURL(entry).href);
