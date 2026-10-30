/**
 * 受限沙箱兼容 preload（仅在检测到「无法 spawn 子进程」时启用）。
 *
 * 检测方式：尝试 `spawnSync(node, ['-e','0'])`。普通环境会成功，此时本文件不做任何
 * 全局修改，Vite / Vitest 使用标准 esbuild；受限环境（EPERM）则启用以下补丁：
 *   1) 把 `exec("net use")` 替换为空实现（Vite 在 Windows 上用于识别网络驱动器）；
 *   2) 让 Vite 的 safe-realpath 探测走 EISDIR 分支，避免再触发 exec；
 *   3) 通过 loader 把 'esbuild' 重定向为纯 JS shim。
 */
import childProcess, { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { register } from 'node:module';
import { EventEmitter } from 'node:events';

let restricted = false;
try {
  const probe = spawnSync(process.execPath, ['-e', '0'], {
    stdio: ['ignore', 'pipe', 'ignore'],
    timeout: 5000,
    windowsHide: true,
  });
  restricted = Boolean(
    probe.error && (probe.error.code === 'EPERM' || probe.error.code === 'EACCES'),
  );
} catch {
  restricted = true;
}

if (restricted) {
  // ---- 1. net use 空实现 ---------------------------------------------------
  function isNetUse(command) {
    return /^\s*net\s+use\b/i.test(String(command));
  }

  function fakeChild() {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.stdin = null;
    child.pid = 0;
    child.kill = () => true;
    return child;
  }

  const originalExec = childProcess.exec;
  childProcess.exec = function patchedExec(command, options, callback) {
    if (isNetUse(command)) {
      const cb = typeof options === 'function' ? options : callback;
      if (typeof cb === 'function') queueMicrotask(() => cb(null, '', ''));
      return fakeChild();
    }
    return originalExec.call(this, command, options, callback);
  };

  const originalExecSync = childProcess.execSync;
  childProcess.execSync = function patchedExecSync(command, options) {
    if (isNetUse(command)) return Buffer.from('');
    return originalExecSync.call(this, command, options);
  };

  // ---- 2. 让 Vite 的 safe-realpath 探测走 EISDIR 分支 ---------------------
  const cwd = path.resolve(process.cwd());
  const originalNative = fs.realpathSync.native;
  let armed = true;

  fs.realpathSync.native = function patchedNative(target, ...rest) {
    if (armed && path.resolve(String(target)) === cwd) {
      armed = false;
      fs.realpathSync.native = originalNative;
      const error = new Error(
        `EISDIR: illegal operation on a directory, realpath '${String(target)}'`,
      );
      error.code = 'EISDIR';
      throw error;
    }
    return originalNative.call(this, target, ...rest);
  };

  // ---- 3. esbuild → 纯 JS shim --------------------------------------------
  register(new URL('./esbuild-loader.mjs', import.meta.url));
}
