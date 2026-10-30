import type { CapacitorConfig } from '@capacitor/cli';

/**
 * Capacitor 手机端配置。
 *
 * 构建：
 *   pnpm build
 *   npx cap add android   # 首次
 *   npx cap sync
 *   npx cap open android
 *
 * iOS：需要 macOS + Xcode，执行 `npx cap add ios` 后 `npx cap open ios`。
 */
const config: CapacitorConfig = {
  appId: 'com.openassistant.app',
  appName: 'OpenAssistant',
  webDir: 'dist',
  bundledWebRuntime: false,
  android: {
    allowMixedContent: true,
  },
  server: {
    androidScheme: 'https',
  },
  plugins: {
    BluetoothLe: {
      displayStrings: {
        scanning: '正在扫描附近的录音端…',
        cancel: '取消',
        availableDevices: '可用设备',
        noDeviceFound: '没有发现 OpenAssistant 设备，请确认录音端已开机并处于配对模式。',
      },
    },
  },
};

export default config;
