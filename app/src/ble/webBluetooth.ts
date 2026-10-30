/**
 * Web Bluetooth 极简封装（仅覆盖 Agent 管理通道需要的 GATT 操作）。
 *
 * 不依赖 @types/web-bluetooth：这里声明最小接口，浏览器不支持时降级。
 */

export const BLE_SERVICE_UUID = '0000a001-0000-1000-8000-00805f9b34fb';
export const CONFIG_CHARACTERISTIC_UUID = '0000a002-0000-1000-8000-00805f9b34fb';
export const STATUS_CHARACTERISTIC_UUID = '0000a003-0000-1000-8000-00805f9b34fb';
export const QUEUE_CHARACTERISTIC_UUID = '0000a004-0000-1000-8000-00805f9b34fb';

/** 协议规定单次写入 ≤512 字节，这里每片 200 字节并顺序追加。 */
export const WRITE_CHUNK_BYTES = 200;
export const MAX_WRITE_BYTES = 512;

export interface GattCharacteristic {
  value?: DataView;
  readValue(): Promise<DataView>;
  writeValue(value: Uint8Array): Promise<void>;
  writeValueWithResponse?(value: Uint8Array): Promise<void>;
  startNotifications?(): Promise<GattCharacteristic>;
  stopNotifications?(): Promise<GattCharacteristic>;
  addEventListener(type: string, listener: (event: Event) => void): void;
  removeEventListener(type: string, listener: (event: Event) => void): void;
}

export interface GattServer {
  connected: boolean;
  connect(): Promise<GattServer>;
  disconnect(): void;
  getPrimaryService(uuid: string): Promise<{
    getCharacteristic(uuid: string): Promise<GattCharacteristic>;
  }>;
  addEventListener?(type: string, listener: () => void): void;
}

export interface BleDevice {
  id: string;
  name?: string;
  gatt?: GattServer;
  addEventListener(type: string, listener: () => void): void;
  removeEventListener?(type: string, listener: () => void): void;
}

interface BluetoothLike {
  requestDevice(options: {
    filters: { namePrefix?: string; services?: string[] }[];
    optionalServices?: string[];
  }): Promise<BleDevice>;
}

export function getBluetooth(): BluetoothLike | null {
  if (typeof navigator === 'undefined') return null;
  const nav = navigator as Navigator & { bluetooth?: BluetoothLike };
  return nav.bluetooth ?? null;
}

export function isWebBluetoothSupported(): boolean {
  return getBluetooth() !== null;
}

export interface AgentConnection {
  device: BleDevice;
  server: GattServer;
  config: GattCharacteristic;
  status: GattCharacteristic;
  queue: GattCharacteristic;
}

export async function connectAgent(): Promise<AgentConnection> {
  const bluetooth = getBluetooth();
  if (!bluetooth) {
    throw new Error(
      '当前浏览器不支持 Web Bluetooth。请使用 Android Chrome / 桌面 Chrome / Edge（HTTPS 或 localhost）。',
    );
  }
  const device = await bluetooth.requestDevice({
    filters: [{ namePrefix: 'OpenAssistant' }],
    optionalServices: [BLE_SERVICE_UUID],
  });
  if (!device.gatt) throw new Error('该设备不支持 GATT 连接');

  const server = await device.gatt.connect();
  const service = await server.getPrimaryService(BLE_SERVICE_UUID);
  const [config, status, queue] = await Promise.all([
    service.getCharacteristic(CONFIG_CHARACTERISTIC_UUID),
    service.getCharacteristic(STATUS_CHARACTERISTIC_UUID),
    service.getCharacteristic(QUEUE_CHARACTERISTIC_UUID),
  ]);
  return { device, server, config, status, queue };
}

export function decodeJson<T>(view: DataView): T {
  const text = new TextDecoder('utf-8').decode(view);
  return JSON.parse(text) as T;
}

export function encodeUtf8(value: unknown): Uint8Array {
  return new TextEncoder().encode(JSON.stringify(value));
}

/**
 * 按 UTF-8 字符边界切片，避免把多字节字符截断。
 * 每片最多 200 字节，总量最多 512 字节。
 */
export function chunkUtf8(value: unknown, chunkSize = WRITE_CHUNK_BYTES): Uint8Array[] {
  const bytes = encodeUtf8(value);
  if (bytes.byteLength > MAX_WRITE_BYTES) {
    throw new Error(`配置 JSON 超过 ${MAX_WRITE_BYTES} 字节，请缩短后重试`);
  }
  const text = new TextDecoder('utf-8').decode(bytes);
  const encoder = new TextEncoder();
  const chunks: Uint8Array[] = [];
  let current: number[] = [];
  for (const char of text) {
    const charBytes = encoder.encode(char);
    if (current.length + charBytes.length > chunkSize) {
      chunks.push(new Uint8Array(current));
      current = [];
    }
    current.push(...charBytes);
  }
  if (current.length) chunks.push(new Uint8Array(current));
  return chunks;
}

/** 分片写入（每片 ≤200 字节，按顺序追加）。 */
export async function writeJson(characteristic: GattCharacteristic, value: unknown): Promise<void> {
  const chunks = chunkUtf8(value);
  for (const chunk of chunks) {
    if (characteristic.writeValueWithResponse) {
      await characteristic.writeValueWithResponse(chunk);
    } else {
      await characteristic.writeValue(chunk);
    }
  }
}

export async function readJson<T>(characteristic: GattCharacteristic): Promise<T> {
  const view = await characteristic.readValue();
  return decodeJson<T>(view);
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** 订阅 STATUS notify，返回取消订阅函数。 */
export async function subscribeStatus(
  characteristic: GattCharacteristic,
  onStatus: (status: unknown) => void,
): Promise<() => void> {
  const handler = () => {
    if (!characteristic.value) return;
    try {
      onStatus(decodeJson(characteristic.value));
    } catch {
      // 片段不是合法 JSON 时忽略，等待下一次 notify
    }
  };
  characteristic.addEventListener('characteristicvaluechanged', handler);
  if (characteristic.startNotifications) {
    await characteristic.startNotifications();
  }
  return () => {
    characteristic.removeEventListener('characteristicvaluechanged', handler);
    if (characteristic.stopNotifications) {
      void characteristic.stopNotifications().catch(() => undefined);
    }
  };
}

export interface AgentStatus {
  device_id?: string;
  wifi_connected?: boolean;
  wifi_ssid?: string;
  queue?: number;
  recording?: boolean;
  uptime_s?: number;
  retry_max?: number;
  offline_buffer?: boolean;
  battery?: number;
  [key: string]: unknown;
}

export interface AgentConfig {
  wifi?: { ssid: string; password: string };
  retry_max?: number;
  offline_buffer?: boolean;
  upload_endpoint?: string;
  vad?: { aggressiveness: number; min_speech_ms: number };
  [key: string]: unknown;
}

export interface QueueResponse {
  action?: string;
  result?: unknown;
  error?: string;
  [key: string]: unknown;
}

export async function requestQueue(
  characteristic: GattCharacteristic,
  action: 'list' | 'retry' | 'clear',
): Promise<QueueResponse | null> {
  await writeJson(characteristic, { action });
  // Agent 处理需要一点时间；顺序重试读取，避免读到旧值。
  for (let attempt = 0; attempt < 5; attempt += 1) {
    await sleep(150 + attempt * 100);
    try {
      const response = await readJson<QueueResponse>(characteristic);
      if (response && response.action === action) return response;
    } catch {
      // 继续重试
    }
  }
  try {
    return await readJson<QueueResponse>(characteristic);
  } catch {
    return null;
  }
}

export function formatUptime(seconds?: number): string {
  if (!seconds || seconds < 0) return '—';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `${hours} 小时 ${minutes} 分`;
  return `${minutes} 分钟`;
}
