/**
 * Vite 启动包装：先加载受限环境 preload，再以真实路径导入 Vite CLI。
 */
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import './preload.mjs';

const entry = fs.realpathSync.native(path.resolve('node_modules/vite/bin/vite.js'));
const args = process.argv.slice(2).filter((arg) => arg !== '--' && arg !== '--configLoader');
process.argv = [process.argv[0], entry, ...args, '--configLoader', 'native'];
await import(pathToFileURL(entry).href);
