# OpenAssistant App

OpenAssistant 的 **手机 App**（Material Design 3）：通过 API Key 访问 Server，通过蓝牙管理树莓派 Agent。
技术栈为 React 19 + TypeScript + Vite 7 + MUI v7，使用 **Capacitor** 打包为 Android / iOS 安装包；
浏览器（Chrome/Edge）可直接运行调试，也可作为 PWA 使用。

- React 19 + TypeScript + Vite 7
- MUI v7（`@mui/material` / `@mui/icons-material` / Emotion），自定义 MD3 色彩 token、圆角与排版，深浅色跟随系统
- react-router-dom v7（`createBrowserRouter`）
- TanStack Query v5（真实 API 数据层 + mutation invalidate）
- date-fns（中文日期格式）
- Capacitor v7（Android / iOS 原生壳）+ `@capacitor-community/bluetooth-le`（原生 BLE）
- 测试：vitest + @testing-library/react + jsdom

---

## 快速开始

```bash
# 安装（本仓库在受限沙箱中开发，Node/pnpm 使用绝对路径调用；普通环境直接 pnpm 即可）
node /path/to/pnpm --dir app install
node /path/to/pnpm --dir app run dev        # http://localhost:5173
node /path/to/pnpm --dir app run build      # tsc --noEmit && vite build → app/dist
node /path/to/pnpm --dir app run preview    # 预览 dist
node /path/to/pnpm --dir app run test -- --run
```

> 依赖安装说明：仓库内的 `pnpm-workspace.yaml` 设置了 `nodeLinker: hoisted` 与 `allowBuilds: esbuild: false`，
> 以便在 Windows 受限沙箱（禁止 Node 启动带管道 stdio 的子进程）中安装依赖。
> 这些设置只影响 `node_modules` 布局和安装脚本，不影响应用代码；普通环境也可以直接使用 npm 安装。

### 推荐的开发访问方式

在 `vite.config.mjs` 中已把 `/api` 代理到 `http://127.0.0.1:8000`，因此：

```bash
python -m uvicorn app.main:app --port 8000     # 在 server/ 中启动 Server
pnpm --dir app run dev
```

也可以把 Server 部署到任意地址，在 App 的「设置 → 服务器连接」中填写完整 URL（`https://host:port`，无需包含 `/api/v1`）。

---

## 打包为手机 App（Capacitor）

`app/android/` 已经生成（包名 `com.openassistant.app`），iOS 目录在 macOS 上生成。
**当前仓库已实际构建出调试安装包**：`app/releases/OpenAssistant-1.0.0-debug.apk`
（4.2 MB，`com.openassistant.app`，compileSdk/targetSdk 35，minSdk 23，已含 BLE 权限）。

### 环境要求

| 依赖 | 版本 / 说明 |
|---|---|
| JDK | **17 或 21**（Gradle 8.11.1 不支持 JDK 25；本机构建使用 `temurin@21.0.11`） |
| Android SDK | `platforms/android-35`、`build-tools/35.0.0`、`build-tools/34.0.0`、`platform-tools` |
| Android CLI（可选） | `android sdk install platforms/android-35` 等命令可代替 Android Studio 安装 SDK |

SDK 与 Gradle 缓存可以放在仓库内（受限环境推荐）：

```powershell
$env:ANDROID_HOME = "C:\...\OpenAssistant\.android-sdk"      # 或你本机 SDK 路径
$env:JAVA_HOME    = "C:\...\jdk\temurin@21.0.11"
$env:GRADLE_USER_HOME = "C:\...\OpenAssistant\.gradle"
# 或在 app/android/local.properties 写入：sdk.dir=C\:\\path\\to\\android-sdk
```

### 构建步骤（已在 Windows + JDK21 + Android SDK 35 实跑通过）

```bash
cd app
pnpm install
pnpm build                  # Web 资源 → dist/
npx cap sync android        # 复制 dist 与 BLE 插件

cd android
./gradlew.bat assembleDebug # Windows；Linux/macOS: ./gradlew assembleDebug
# 产物：android/app/build/outputs/apk/debug/app-debug.apk
# 仓库副本：app/releases/OpenAssistant-1.0.0-debug.apk
```

也可以使用一键脚本（自动探测 JDK/SDK、构建并复制产物）：

```powershell
# Windows 可能禁止运行未签名的 .ps1，可临时用：
powershell -ExecutionPolicy Bypass -File scripts/build-android.ps1
# 或指定路径：
./scripts/build-android.ps1 -JdkHome "C:\Java\jdk-21" -AndroidSdk "C:\Android\Sdk"
```

安装到手机（开启 USB 调试）：

```bash
adb install -r app/releases/OpenAssistant-1.0.0-debug.apk
```

> 已构建产物 SHA256：`465DFF0959D99EB373E26803A311CEE1143C85A5584F1C427946706AD475BA28`

也可以 `npx cap open android` 用 Android Studio 打开工程构建 APK/AAB。
签名：debug 包使用仓库内 `android/keystore/debug.keystore`（密码 `android`，仅用于调试）；
正式发布请创建自己的 release keystore 并配置 `android/app/build.gradle` 的 `signingConfigs.release`，
同时建议修改 `applicationId`。

说明：

- **BLE 平台适配**：`src/platform/ble.ts` 在原生端使用 `@capacitor-community/bluetooth-le`，
  在浏览器端自动降级到 Web Bluetooth；页面只依赖该适配层。
- Android 权限已在 `android/app/src/main/AndroidManifest.xml` 中声明
  （`BLUETOOTH_SCAN` / `BLUETOOTH_CONNECT`，Android 11 及以下另需定位权限）；
  首次连接时会弹出系统权限/配对请求。
- **明文 HTTP**：为方便连接局域网自建 Server，Manifest 中开启了 `usesCleartextTraffic`；
  生产环境建议改用 HTTPS 并关闭该开关。
- 未安装 Android SDK 时无法在本机出包，但 `pnpm build` / `npx cap sync` 均可验证 Web 资源与插件配置正确。

---

## 功能与页面

| 路由 | 页面 | 说明 |
|---|---|---|
| `/` | 首页 | 今日日程提醒、待办（勾选完成 / 新建 / 删除）、指令结果通知；刷新与 FAB |
| `/schedule` | 日程 | 未来 / 过去分区、按日分组；新建、编辑、删除；来源标签；跳转来源卡片 |
| `/history` | 历史记录 | 卡片列表；关键词搜索（`GET /cards?q=`）、类型与时间范围筛选；身份标签统计 |
| `/history/:id` | 卡片详情 | 总结 / 关键信息 / 人物 / 类型徽章；ASR 原文按 turn 展示 `[使用者]`/`[其他人]`；下载原始录音；导出原文；勘误；级联删除确认；勘误历史；关联日程/待办/指令 |
| `/meetings` | 会议纪要 | 会议列表（参与者、行动项数量、状态） |
| `/meetings/:id` | 会议详情 | 议题、决议、行动项、参与者、Markdown 纪要；重新生成；编辑标题/摘要/纪要；删除 |
| `/me` | 我的 | 用户画像查看/编辑（≤600 字符、字数统计）/重新生成；多姓名称呼 Chips 增删并同步；声纹状态与音频上传；配置入口 |
| `/settings` | 设置 | 服务器地址 + API Key（localStorage）、测试连接（`/health`、`/me`）、搜索工具选择、四项通知/自动化开关、钉钉推送自检、审计记录、演示模式 |
| `/device` | 设备管理 | Web Bluetooth 连接、状态、WiFi/重传/离线暂存写入、队列 list/retry/clear、Server 备份配置、连接日志 |

布局：移动端底部导航（首页 / 日程 / 历史 / 会议 / 我的）+ 顶部 AppBar（标题、刷新、设置）；桌面端左侧固定抽屉。演示模式下顶部常驻「演示数据」提示条。

通用体验：MUI Card / FAB / Chip / Snackbar / Skeleton / EmptyState / ConfirmDialog；`env(safe-area-inset-*)` 安全区；中文界面；语义化标题与 `aria-label`。

---

## 数据层

```
src/api/
  types.ts     # 与 docs/api.md 字段完全一致的 snake_case 类型
  client.ts    # fetch 封装：X-API-Key、超时、401/错误体解析、音频下载
  demo.ts      # 中文 fixtures + 内存写操作
  queries.ts   # TanStack Query hooks 与 cache key
```

- 所有请求的基础路径为 `{serverUrl}/api/v1`；`serverUrl` 留空时使用同源 `/api/v1`。
- 认证头：`X-API-Key`。
- 超时：普通请求 20s，音频上传/重新生成纪要 120s；超时与网络错误会转成带中文提示的 `ApiError`。
- `GET /audio/{segment_id}/file` 需要认证，下载通过 `fetch` 带 Key 后生成 Blob（演示模式生成静音 WAV）。
- 配置保存在 `localStorage["openassistant.config.v1"]`，包含 `serverUrl` / `apiKey` / `demoMode`。

### 演示模式

满足任一条件即启用：

1. 未填写 API Key；或
2. 在设置页显式打开「演示模式」。

演示数据全部为中文虚构内容（4 张卡片、7 条日程、5 条待办、2 场会议、4 条指令、审计记录、用户画像、声纹）。写操作（待办勾选、日程/会议/画像/设置更新、勘误、删除、队列配置备份等）都在内存中生效并走 TanStack Query 失效重取，不会产生真实数据。

切换服务器 / API Key / 演示开关时会清空 Query 缓存，立即按新配置重新取数。

---

## 设备管理（BLE）

- **原生 App**：Android/iOS 通过 `@capacitor-community/bluetooth-le` 连接（`src/platform/ble.ts` 原生实现）；
- **浏览器**：Android Chrome / 桌面 Chrome / Edge，页面必须运行在 HTTPS 或 localhost（Web Bluetooth 安全上下文限制）；
- 不可用时显示说明，并仍可从 `GET /devices` 查看 Server 备份配置。

协议（与 Agent 端约定）：

| 特征 | UUID | 权限 | 内容 |
|---|---|---|---|
| Service | `0000a001-0000-1000-8000-00805f9b34fb` | — | Agent 管理通道 |
| CONFIG | `0000a002-…` | read/write | UTF-8 JSON，≤512 字节 |
| STATUS | `0000a003-…` | read/notify | UTF-8 JSON（device_id / wifi_connected / queue / recording / uptime_s / retry_max / offline_buffer） |
| QUEUE | `0000a004-…` | read/write | `{"action":"list|retry|clear"}` |

- CONFIG 写入按 **UTF-8 字符边界切分为 ≤200 字节的分片**，使用 `writeValueWithResponse` 顺序追加；总长度超过 512 字节会在本地直接报错。
- 写入成功后会调用 `PUT /devices/{device_id}/config` 做 Server 备份；备份失败只提示，不影响设备配置。
- 队列操作写入 action 后读取 QUEUE 结果并展示原始 JSON。
- 连接日志面板会记录关键步骤，便于现场排查。

---

## PWA / 部署

- `index.html` 带 `viewport-fit=cover`、`theme-color`、manifest 与 SVG 图标。
- `public/sw.js` 为最小 Service Worker：导航网络优先、静态资源缓存优先，`/api/` 永远走网络；仅在 **生产构建** 中注册。
- `pnpm build` 后把 `app/dist` 交给 Nginx（需为 SPA 配置 `try_files $uri /index.html`）。
- 手机安装包使用 Capacitor 构建（见上文「打包为手机 App」）。
- 注意：iOS 不允许使用 Web Bluetooth，原生包使用 BLE 插件，因此 iOS 安装包同样可以连接录音端。

---

## 测试

```bash
pnpm run test -- --run
```

覆盖：

- `src/test/client.test.ts`：未配置 Key / 演示开关时走 fixtures 且不发请求；配置后请求 URL 与 `X-API-Key`；401 抛出 `ApiError`；演示模式写操作内存生效。
- `src/test/home.test.tsx`：勾选待办 → `PATCH /todos/{id}`、提示、移入已完成。
- `src/test/cardDetail.test.tsx`：身份标签 `[使用者]`/`[其他人]` 与 ASR turn 渲染；勘误 `PATCH /cards/{id}`；删除确认、级联说明、`DELETE /cards/{id}`。
- `src/test/app.test.tsx`：应用外壳（AppBar / 演示提示 / 底部导航 / 首页数据）冒烟。

---

## 本仓库的构建适配（沙箱环境）

当前执行环境禁止 Node 以带管道 stdio 的方式启动子进程，因此 esbuild 的 service 进程、Vitest 的 fork pool 都无法使用。`app/` 内做了以下等价适配（不影响业务代码）：

1. `vite.config.mjs` / `vitest.config.mjs` 关闭 esbuild，使用 **Babel**（`@babel/preset-typescript` + `@babel/preset-react` automatic runtime）转换 TS/TSX，产物用 **terser** 压缩（`scripts/babel-plugin.mjs`）。
2. `scripts/preload.mjs`：仅在检测到「无法 spawn 子进程」时启用补丁——屏蔽 Vite 在 Windows 上的 `net use` 探测、规避 `fs.realpathSync.native` 的 junction 问题，并通过 Node loader 把 `esbuild` 重定向为纯 JS shim（`scripts/esbuild-shim.mjs`，只实现 Vite 仍会调用的 define 替换）。普通环境检测失败即不启用，Vite 使用标准 esbuild。
3. `scripts/vite.mjs` / `scripts/test.mjs`：以 realpath 导入 Vite/Vitest，并追加 `--configLoader native`。
4. Vitest 使用 `pool: 'threads'`（worker_threads）替代 fork。

> 受限环境的副作用：`vite dev` 的依赖预构建（optimizeDeps）依赖 esbuild service，会提示 “Failed to run dependency scan”，浏览器中部分 CJS 依赖可能无法加载。请在该环境使用 `pnpm build && pnpm preview` 验证生产行为；普通开发机不受影响。

普通环境如希望恢复标准 Vite 构建，可移除上述 preload/shim，改回 `@vitejs/plugin-react` 与默认 esbuild 即可，页面代码无需改动。

---

## 已知限制

- BLE 相关逻辑基于 Web Bluetooth 类型最小声明，未在真实树莓派上做端到端联调；部分特征读取可能因 Agent 固件实现而需要调整超时/重试。
- 协议未定义电量字段，只有在 STATUS 返回 `battery` 时才展示电量。
- 声纹注册使用文件选择上传；未实现浏览器内录音（协议未要求，文件上传已覆盖）。
- 会议纪要 Markdown 使用内置轻量渲染，支持标题/列表/引用/粗体/行内代码，不支持表格、图片等完整 Markdown 语法。
- 演示模式下载的音频为静音 WAV 占位文件，仅用于验证交互。
- 桌面端刷新按钮会刷新当前所有活动查询；无离线写入队列（写操作依赖网络）。
- 受限沙箱内 `vite dev` 的依赖预构建不可用（esbuild 无法启动），请用 `build` + `preview`；普通开发环境无此限制。
