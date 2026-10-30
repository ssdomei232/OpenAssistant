/**
 * BLE 平台适配层。
 *
 * - 原生（Capacitor Android/iOS）：使用 @capacitor-community/bluetooth-le 原生插件；
 * - 浏览器（Web）：使用 Web Bluetooth（Chrome/Edge）。
 *
 * 上层页面只依赖本模块，无需关心运行平台。
 * 协议与 Agent 端一致：service a001，CONFIG a002，STATUS a003，QUEUE a004。
 */
import { Capacitor } from '@capacitor/core';
import {
  BLE_SERVICE_UUID,
  CONFIG_CHARACTERISTIC_UUID,
  MAX_WRITE_BYTES,
  QUEUE_CHARACTERISTIC_UUID,
  STATUS_CHARACTERISTIC_UUID,
  chunkUtf8,
  connectAgent as connectWebAgent,
  formatUptime,
  isWebBluetoothSupported,
  readJson,
  requestQueue,
  subscribeStatus,
  writeJson,
  type AgentConfig,
  type AgentStatus,
  type GattCharacteristic,
  type QueueResponse,
} from '../ble/webBluetooth';

export {
  BLE_SERVICE_UUID,
  CONFIG_CHARACTERISTIC_UUID,
  MAX_WRITE_BYTES,
  QUEUE_CHARACTERISTIC_UUID,
  STATUS_CHARACTERISTIC_UUID,
  chunkUtf8,
  formatUptime,
  readJson,
  requestQueue,
  subscribeStatus,
  writeJson,
};
export type { AgentConfig, AgentStatus, GattCharacteristic, QueueResponse };

export interface BleConnection {
  device: {
    id: string;
    name?: string;
    addEventListener(type: string, listener: () => void): void;
    removeEventListener?(type: string, listener: () => void): void;
    gatt?: { connect(): Promise<unknown>; disconnect(): void; connected: boolean };
  };
  server: { disconnect(): void };
  config: GattCharacteristic;
  status: GattCharacteristic;
  queue: GattCharacteristic;
  disconnect?: () => Promise<void>;
}

export type AgentConnection = BleConnection;

export interface BleSupport {
  supported: boolean;
  platform: 'native' | 'web' | 'unsupported';
  message: string;
}

const NATIVE_IDS = {
  service: BLE_SERVICE_UUID,
  config: CONFIG_CHARACTERISTIC_UUID,
  status: STATUS_CHARACTERISTIC_UUID,
  queue: QUEUE_CHARACTERISTIC_UUID,
};

export function isNativeApp(): boolean {
  try {
    return Capacitor.isNativePlatform();
  } catch {
    return false;
  }
}

export function getBleSupport(): BleSupport {
  if (isNativeApp()) {
    return {
      supported: true,
      platform: 'native',
      message: '原生蓝牙（Android/iOS）。请先按下录音端配对键，再点击“连接设备”。',
    };
  }
  if (isWebBluetoothSupported()) {
    return {
      supported: true,
      platform: 'web',
      message: 'Web Bluetooth（Chrome/Edge，需 HTTPS 或 localhost）。',
    };
  }
  return {
    supported: false,
    platform: 'unsupported',
    message:
      '当前环境不支持蓝牙：请使用 Android/iOS 安装包，或桌面 Chrome/Edge（HTTPS/localhost）。' +
      '你仍然可以在下方查看 Server 备份的设备配置。',
  };
}

export function isBleSupported(): boolean {
  return getBleSupport().supported;
}

// ── 原生实现 ──────────────────────────────────────────────────────────────
type AnyRecord = Record<string, unknown>;

function toDataView(value: DataView | Uint8Array): DataView {
  if (value instanceof DataView) return value;
  return new DataView(value.buffer, value.byteOffset, value.byteLength);
}

class NativeCharacteristic implements GattCharacteristic {
  value?: DataView;
  private listeners = new Map<string, Set<(event: Event) => void>>();
  private notificationsStarted = false;
  private notificationHandle?: { remove: () => Promise<void> };
  private readonly client: any;
  private readonly deviceId: string;
  private readonly characteristic: string;
  private readonly onCleanup: (fn: () => void) => void;

  constructor(
    client: any,
    deviceId: string,
    characteristic: string,
    onCleanup: (fn: () => void) => void,
  ) {
    this.client = client;
    this.deviceId = deviceId;
    this.characteristic = characteristic;
    this.onCleanup = onCleanup;
  }

  async readValue(): Promise<DataView> {
    const result = await this.client.read({
      deviceId: this.deviceId,
      service: NATIVE_IDS.service,
      characteristic: this.characteristic,
    });
    this.value = toDataView(result.value);
    return this.value;
  }

  async writeValue(value: Uint8Array): Promise<void> {
    await this.writeValueWithResponse(value);
  }

  async writeValueWithResponse(value: Uint8Array): Promise<void> {
    await this.client.write({
      deviceId: this.deviceId,
      service: NATIVE_IDS.service,
      characteristic: this.characteristic,
      value: toDataView(value),
      type: 'write' as never,
    });
  }

  async startNotifications(): Promise<GattCharacteristic> {
    if (this.notificationsStarted) return this;
    this.notificationsStarted = true;
    await this.client.startNotifications({
      deviceId: this.deviceId,
      service: NATIVE_IDS.service,
      characteristic: this.characteristic,
    });
    const listener = (event: AnyRecord) => {
      const characteristic = (event.characteristic as string) ?? '';
      if (characteristic && characteristic.toLowerCase() !== this.characteristic.toLowerCase()) return;
      const value = event.value as DataView | Uint8Array | undefined;
      if (!value) return;
      this.value = toDataView(value);
      this.dispatch();
    };
    const registration = await this.client.addListener('notification', listener);
    this.notificationHandle = { remove: async () => registration.remove() };
    this.onCleanup(() => {
      void this.stopNotifications();
    });
    return this;
  }

  async stopNotifications(): Promise<GattCharacteristic> {
    this.notificationsStarted = false;
    try {
      await this.notificationHandle?.remove();
    } catch {
      // 断开连接后移除监听可能失败，忽略
    }
    this.notificationHandle = undefined;
    try {
      await this.client.stopNotifications({
        deviceId: this.deviceId,
        service: NATIVE_IDS.service,
        characteristic: this.characteristic,
      });
    } catch {
      // 忽略
    }
    return this;
  }

  addEventListener(type: string, listener: (event: Event) => void): void {
    const set = this.listeners.get(type) ?? new Set();
    set.add(listener);
    this.listeners.set(type, set);
  }

  removeEventListener(type: string, listener: (event: Event) => void): void {
    this.listeners.get(type)?.delete(listener);
  }

  private dispatch(): void {
    for (const listener of this.listeners.get('characteristicvaluechanged') ?? []) {
      try {
        listener(new Event('characteristicvaluechanged'));
      } catch {
        // 单个监听器异常不影响其它监听器
      }
    }
  }
}

interface NativeConnection extends AgentConnection {
  deviceId: string;
}

async function connectNative(): Promise<NativeConnection> {
  const { BluetoothLe } = (await import('@capacitor-community/bluetooth-le')) as unknown as {
    BluetoothLe: any;
  };
  await BluetoothLe.initialize({ request: true });
  const device = await BluetoothLe.requestDevice({
    services: [BLE_SERVICE_UUID],
    namePrefix: 'OpenAssistant',
    optionalServices: [BLE_SERVICE_UUID],
  });
  const deviceId: string = device.deviceId;
  await BluetoothLe.connect(deviceId, {
    onDisconnected: () => {
      disconnectListeners.forEach((listener) => listener());
    },
  });

  const cleanups: Array<() => void> = [];
  const disconnectListeners = new Set<() => void>();
  const config = new NativeCharacteristic(BluetoothLe, deviceId, NATIVE_IDS.config, (fn) =>
    cleanups.push(fn),
  );
  const status = new NativeCharacteristic(BluetoothLe, deviceId, NATIVE_IDS.status, (fn) =>
    cleanups.push(fn),
  );
  const queue = new NativeCharacteristic(BluetoothLe, deviceId, NATIVE_IDS.queue, (fn) =>
    cleanups.push(fn),
  );

  let disconnected = false;
  const disconnect = async () => {
    if (disconnected) return;
    disconnected = true;
    cleanups.forEach((fn) => {
      try {
        fn();
      } catch {
        // ignore
      }
    });
    try {
      await BluetoothLe.disconnect(deviceId);
    } catch {
      // ignore
    }
    disconnectListeners.forEach((listener) => listener());
  };

  const deviceView = {
    id: deviceId,
    name: device.name ?? device.localName ?? deviceId,
    addEventListener(type: string, listener: () => void) {
      if (type === 'gattserverdisconnected') disconnectListeners.add(listener);
    },
    removeEventListener(type: string, listener: () => void) {
      if (type === 'gattserverdisconnected') disconnectListeners.delete(listener);
    },
    gatt: { connected: true, connect: async () => undefined, disconnect: () => void disconnect() },
  };

  return {
    device: deviceView,
    server: { disconnect: () => void disconnect() },
    config,
    status,
    queue,
    disconnect,
    deviceId,
  };
}

// ── 统一入口 ──────────────────────────────────────────────────────────────
export async function connectAgent(): Promise<AgentConnection> {
  if (isNativeApp()) {
    return connectNative();
  }
  return connectWebAgent();
}
