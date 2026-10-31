/**
 * 统一 API 客户端。
 *
 * - 自动附加 `X-API-Key`
 * - 超时、401、错误体解析
 * - 未配置 API Key 或开启「演示模式」时，全部读写走内置 fixtures
 */
import { demoApi } from './demo';
import type {
  AuditEntry,
  AuditQuery,
  CardDeleteResult,
  CardDetail,
  CardMergeResult,
  CardPatch,
  CardQuery,
  CardSummary,
  Command,
  CommandQuery,
  DeleteResult,
  Device,
  DeviceConfig,
  DeviceConfigSaved,
  Health,
  Me,
  Meeting,
  MeetingDetail,
  MeetingPatch,
  MeetingQuery,
  MeSettings,
  Paged,
  Profile,
  PushTestResponse,
  ScheduleCreate,
  ScheduleItem,
  SchedulePatch,
  ScheduleQuery,
  SearchProvider,
  SearchResponse,
  Todo,
  TodoCreate,
  TodoPatch,
  TodoQuery,
  VoiceprintState,
} from './types';

export const CONFIG_STORAGE_KEY = 'openassistant.config.v1';
export const DEFAULT_SERVER_URL = '';
const DEFAULT_TIMEOUT_MS = 20_000;

export interface AppConfig {
  serverUrl: string;
  apiKey: string;
  demoMode: boolean;
}

export const DEFAULT_CONFIG: AppConfig = {
  serverUrl: DEFAULT_SERVER_URL,
  apiKey: '',
  demoMode: false,
};

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly extra?: unknown;

  constructor(status: number, detail: string, message?: string, extra?: unknown) {
    super(message ?? detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.extra = extra;
  }
}

function canUseStorage(): boolean {
  try {
    return typeof window !== 'undefined' && !!window.localStorage;
  } catch {
    return false;
  }
}

export function loadConfig(): AppConfig {
  if (!canUseStorage()) return { ...DEFAULT_CONFIG };
  try {
    const raw = window.localStorage.getItem(CONFIG_STORAGE_KEY);
    if (!raw) return { ...DEFAULT_CONFIG };
    const parsed = JSON.parse(raw) as Partial<AppConfig>;
    return {
      serverUrl: typeof parsed.serverUrl === 'string' ? parsed.serverUrl : '',
      apiKey: typeof parsed.apiKey === 'string' ? parsed.apiKey : '',
      demoMode: parsed.demoMode === true,
    };
  } catch {
    return { ...DEFAULT_CONFIG };
  }
}

export function saveConfig(patch: Partial<AppConfig>): AppConfig {
  const next: AppConfig = { ...loadConfig(), ...patch };
  if (canUseStorage()) {
    try {
      window.localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(next));
    } catch {
      // 隐私模式下 localStorage 写入失败时保持内存态即可
    }
  }
  return next;
}

export function resetConfig(): AppConfig {
  if (canUseStorage()) {
    try {
      window.localStorage.removeItem(CONFIG_STORAGE_KEY);
    } catch {
      // ignore
    }
  }
  return { ...DEFAULT_CONFIG };
}

/** 演示模式：显式开启，或尚未配置 API Key。 */
export function isDemoMode(config: AppConfig = loadConfig()): boolean {
  return config.demoMode || config.apiKey.trim().length === 0;
}

export function apiBaseUrl(config: AppConfig = loadConfig()): string {
  const server = config.serverUrl.trim().replace(/\/+$/, '');
  if (!server) return '/api/v1';
  if (server.endsWith('/api/v1')) return server;
  return `${server}/api/v1`;
}

function serverOrigin(config: AppConfig = loadConfig()): string {
  const server = config.serverUrl.trim().replace(/\/+$/, '');
  return server.replace(/\/api\/v1$/, '');
}

function buildUrl(
  path: string,
  params?: Record<string, string | number | boolean | undefined>,
  config: AppConfig = loadConfig(),
): string {
  const base = apiBaseUrl(config);
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  const url = `${base}${normalizedPath}`;
  if (!params) return url;
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === '' || value === null) continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `${url}?${qs}` : url;
}

/** 把 API 返回的 audio_url 转成可直接请求的绝对地址。 */
export function absoluteAudioUrl(audioUrl: string, config: AppConfig = loadConfig()): string {
  if (/^https?:\/\//i.test(audioUrl)) return audioUrl;
  const origin = serverOrigin(config);
  if (audioUrl.startsWith('/api/v1')) return `${origin}${audioUrl}`;
  if (audioUrl.startsWith('/')) return `${origin}${audioUrl}`;
  return buildUrl(audioUrl, undefined, config);
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  formData?: FormData;
  query?: Record<string, string | number | boolean | undefined>;
  signal?: AbortSignal;
  timeoutMs?: number;
  /** 强制走真实网络（用于设置页「测试连接」）。 */
  forceLive?: boolean;
  expectText?: boolean;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const config = loadConfig();
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const abortFromCaller = () => controller.abort();
  options.signal?.addEventListener('abort', abortFromCaller);

  const headers: Record<string, string> = { Accept: 'application/json' };
  if (config.apiKey.trim()) headers['X-API-Key'] = config.apiKey.trim();
  let body: BodyInit | undefined;
  if (options.formData) {
    body = options.formData;
  } else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(options.body);
  }

  try {
    const response = await fetch(buildUrl(path, options.query, config), {
      method: options.method ?? 'GET',
      headers,
      body,
      signal: controller.signal,
    });
    if (!response.ok) {
      let detail = `http_${response.status}`;
      let message = `请求失败（HTTP ${response.status}）`;
      let extra: unknown;
      try {
        const payload = (await response.json()) as {
          detail?: string;
          message?: string;
          extra?: unknown;
        };
        if (payload?.detail) detail = payload.detail;
        if (payload?.message) message = payload.message;
        extra = payload?.extra;
      } catch {
        // 非 JSON 错误体
      }
      if (response.status === 401) {
        message = 'API Key 无效或已过期，请在设置中检查。';
      }
      throw new ApiError(response.status, detail, message, extra);
    }
    if (options.expectText) {
      return (await response.text()) as T;
    }
    if (response.status === 204) return undefined as T;
    const text = await response.text();
    if (!text) return undefined as T;
    return JSON.parse(text) as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(0, 'timeout', '请求超时，请检查服务器地址或网络连接。');
    }
    throw new ApiError(0, 'network_error', '无法连接服务器，请检查服务器地址与网络。');
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener('abort', abortFromCaller);
  }
}

function demoOrLive<T>(demoFn: () => Promise<T>, liveFn: () => Promise<T>): Promise<T> {
  if (isDemoMode()) return demoFn();
  return liveFn();
}

function todayIso(): string {
  const d = new Date();
  return d.toISOString();
}

export const api = {
  // -------------------------------------------------------------------------
  // 健康检查 / 连接测试
  // -------------------------------------------------------------------------
  async getHealth(): Promise<Health> {
    return request<Health>('/health', { forceLive: true });
  },

  async testConnection(): Promise<{ health: Health; me: Me }> {
    const config = loadConfig();
    if (!config.apiKey.trim()) {
      throw new ApiError(401, 'missing_api_key', '尚未配置 API Key，无法连接服务器。');
    }
    const health = await request<Health>('/health');
    const me = await request<Me>('/me');
    return { health, me };
  },

  // -------------------------------------------------------------------------
  // 账号与设置
  // -------------------------------------------------------------------------
  getMe(): Promise<Me> {
    return demoOrLive(
      () => demoApi.getMe(),
      () => request<Me>('/me'),
    );
  },

  updateMe(
    patch: Partial<Pick<Me, 'display_name' | 'aliases' | 'timezone' | 'search_provider'>>,
  ): Promise<Me> {
    return demoOrLive(
      () => demoApi.patchMe(patch),
      () => request<Me>('/me', { method: 'PATCH', body: patch }),
    );
  },

  getSettings(): Promise<MeSettings> {
    return demoOrLive(
      () => demoApi.getSettings(),
      () => request<MeSettings>('/me/settings'),
    );
  },

  updateSettings(patch: Partial<MeSettings>): Promise<MeSettings> {
    return demoOrLive(
      () => demoApi.patchSettings(patch),
      () => request<MeSettings>('/me/settings', { method: 'PATCH', body: patch }),
    );
  },

  // -------------------------------------------------------------------------
  // 画像与声纹
  // -------------------------------------------------------------------------
  getProfile(): Promise<Profile> {
    return demoOrLive(
      () => demoApi.getProfile(),
      () => request<Profile>('/me/profile'),
    );
  },

  updateProfile(text: string): Promise<Profile> {
    return demoOrLive(
      () => demoApi.putProfile(text),
      () => request<Profile>('/me/profile', { method: 'PUT', body: { text } }),
    );
  },

  refreshProfile(): Promise<Profile> {
    return demoOrLive(
      () => demoApi.refreshProfile(),
      () => request<Profile>('/me/profile/refresh', { method: 'POST' }),
    );
  },

  getVoiceprint(): Promise<VoiceprintState> {
    return demoOrLive(
      () => demoApi.getVoiceprint(),
      () => request<VoiceprintState>('/me/voiceprint'),
    );
  },

  uploadVoiceprint(
    file: File,
    threshold?: number,
  ): Promise<{ enrolled: boolean; threshold: number; quality: number; duration_s: number; updated_at: string }> {
    return demoOrLive(
      () => demoApi.uploadVoiceprint(file, threshold),
      () => {
        const form = new FormData();
        form.append('file', file);
        if (typeof threshold === 'number') form.append('threshold', String(threshold));
        return request('/me/voiceprint', { method: 'POST', formData: form, timeoutMs: 120_000 });
      },
    );
  },

  deleteVoiceprint(): Promise<DeleteResult> {
    return demoOrLive(
      () => demoApi.deleteVoiceprint(),
      () => request<DeleteResult>('/me/voiceprint', { method: 'DELETE' }),
    );
  },

  // -------------------------------------------------------------------------
  // 卡片
  // -------------------------------------------------------------------------
  getCards(query: CardQuery = {}): Promise<Paged<CardSummary>> {
    return demoOrLive(
      () => demoApi.getCards(query),
      () =>
        request<Paged<CardSummary>>('/cards', {
          query: {
            q: query.q,
            from: query.from,
            to: query.to,
            type: query.type,
            speaker: query.speaker,
            limit: query.limit,
            cursor: query.cursor,
            include_deleted: query.include_deleted,
          },
        }),
    );
  },

  getCard(id: string): Promise<CardDetail> {
    return demoOrLive(
      () => demoApi.getCard(id),
      () => request<CardDetail>(`/cards/${encodeURIComponent(id)}`),
    );
  },

  updateCard(id: string, patch: CardPatch): Promise<CardDetail> {
    return demoOrLive(
      () => demoApi.patchCard(id, patch),
      () => request<CardDetail>(`/cards/${encodeURIComponent(id)}`, { method: 'PATCH', body: patch }),
    );
  },

  deleteCard(id: string): Promise<CardDeleteResult> {
    return demoOrLive(
      () => demoApi.deleteCard(id),
      () => request<CardDeleteResult>(`/cards/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    );
  },

  restoreCard(id: string): Promise<{ restored: boolean }> {
    return demoOrLive(
      async () => ({ restored: true }),
      () =>
        request<{ restored: boolean }>(`/cards/${encodeURIComponent(id)}/restore`, {
          method: 'POST',
        }),
    );
  },

  getTranscriptText(id: string): Promise<string> {
    return demoOrLive(
      () => demoApi.getTranscriptText(id),
      () =>
        request<string>(`/cards/${encodeURIComponent(id)}/transcript.txt`, {
          expectText: true,
        }),
    );
  },

  /** 手动合并多张卡片并重新总结（App 历史记录页多选合并）。 */
  mergeCards(cardIds: string[], note?: string): Promise<CardMergeResult> {
    return demoOrLive(
      () => demoApi.mergeCards(cardIds, note),
      () =>
        request<CardMergeResult>('/cards/merge', {
          method: 'POST',
          body: { card_ids: cardIds, note: note ?? '' },
          timeoutMs: 120_000,
        }),
    );
  },

  // -------------------------------------------------------------------------
  // 日程
  // -------------------------------------------------------------------------
  getSchedule(query: ScheduleQuery = {}): Promise<{ items: ScheduleItem[] }> {
    return demoOrLive(
      () => demoApi.getSchedule(query),
      () =>
        request<{ items: ScheduleItem[] }>('/schedule', {
          query: { from: query.from, to: query.to, include_deleted: query.include_deleted },
        }),
    );
  },

  createSchedule(payload: ScheduleCreate): Promise<ScheduleItem> {
    return demoOrLive(
      () => demoApi.createSchedule(payload),
      () => request<ScheduleItem>('/schedule', { method: 'POST', body: payload }),
    );
  },

  updateSchedule(id: string, patch: SchedulePatch): Promise<ScheduleItem> {
    return demoOrLive(
      () => demoApi.updateSchedule(id, patch),
      () =>
        request<ScheduleItem>(`/schedule/${encodeURIComponent(id)}`, {
          method: 'PATCH',
          body: patch,
        }),
    );
  },

  deleteSchedule(id: string): Promise<DeleteResult> {
    return demoOrLive(
      () => demoApi.deleteSchedule(id),
      () => request<DeleteResult>(`/schedule/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    );
  },

  // -------------------------------------------------------------------------
  // 待办
  // -------------------------------------------------------------------------
  getTodos(query: TodoQuery = {}): Promise<Paged<Todo>> {
    return demoOrLive(
      () => demoApi.getTodos(query),
      () =>
        request<Paged<Todo>>('/todos', {
          query: { status: query.status, limit: query.limit, cursor: query.cursor },
        }),
    );
  },

  createTodo(payload: TodoCreate): Promise<Todo> {
    return demoOrLive(
      () => demoApi.createTodo(payload),
      () => request<Todo>('/todos', { method: 'POST', body: payload }),
    );
  },

  updateTodo(id: string, patch: TodoPatch): Promise<Todo> {
    return demoOrLive(
      () => demoApi.updateTodo(id, patch),
      () => request<Todo>(`/todos/${encodeURIComponent(id)}`, { method: 'PATCH', body: patch }),
    );
  },

  deleteTodo(id: string): Promise<DeleteResult> {
    return demoOrLive(
      () => demoApi.deleteTodo(id),
      () => request<DeleteResult>(`/todos/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    );
  },

  // -------------------------------------------------------------------------
  // 会议
  // -------------------------------------------------------------------------
  getMeetings(query: MeetingQuery = {}): Promise<Paged<Meeting>> {
    return demoOrLive(
      () => demoApi.getMeetings(query),
      () =>
        request<Paged<Meeting>>('/meetings', {
          query: { from: query.from, to: query.to, limit: query.limit, cursor: query.cursor },
        }),
    );
  },

  getMeeting(id: string): Promise<MeetingDetail> {
    return demoOrLive(
      () => demoApi.getMeeting(id),
      () => request<MeetingDetail>(`/meetings/${encodeURIComponent(id)}`),
    );
  },

  regenerateMeeting(id: string): Promise<MeetingDetail> {
    return demoOrLive(
      () => demoApi.regenerateMeeting(id),
      () =>
        request<MeetingDetail>(`/meetings/${encodeURIComponent(id)}/regenerate`, {
          method: 'POST',
          timeoutMs: 120_000,
        }),
    );
  },

  updateMeeting(id: string, patch: MeetingPatch): Promise<MeetingDetail> {
    return demoOrLive(
      () => demoApi.patchMeeting(id, patch),
      () =>
        request<MeetingDetail>(`/meetings/${encodeURIComponent(id)}`, {
          method: 'PATCH',
          body: patch,
        }),
    );
  },

  deleteMeeting(id: string): Promise<DeleteResult> {
    return demoOrLive(
      () => demoApi.deleteMeeting(id),
      () => request<DeleteResult>(`/meetings/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    );
  },

  // -------------------------------------------------------------------------
  // 指令 / 审计 / 推送
  // -------------------------------------------------------------------------
  getCommands(query: CommandQuery = {}): Promise<Paged<Command>> {
    return demoOrLive(
      () => demoApi.getCommands(query),
      () =>
        request<Paged<Command>>('/commands', {
          query: { status: query.status, limit: query.limit, cursor: query.cursor },
        }),
    );
  },

  retryCommand(id: string): Promise<Command> {
    return demoOrLive(
      () => demoApi.retryCommand(id),
      () => request<Command>(`/commands/${encodeURIComponent(id)}/retry`, { method: 'POST' }),
    );
  },

  getAudit(query: AuditQuery = {}): Promise<Paged<AuditEntry>> {
    return demoOrLive(
      () => demoApi.getAudit(query),
      () =>
        request<Paged<AuditEntry>>('/audit', {
          query: { limit: query.limit, cursor: query.cursor },
        }),
    );
  },

  pushTest(text = 'OpenAssistant 测试消息'): Promise<PushTestResponse> {
    return demoOrLive(
      () => demoApi.pushTest(text),
      () => request<PushTestResponse>('/push/test', { method: 'POST', body: { text } }),
    );
  },

  // -------------------------------------------------------------------------
  // 设备备份
  // -------------------------------------------------------------------------
  getDevices(): Promise<{ items: Device[] }> {
    return demoOrLive(
      () => demoApi.getDevices(),
      () => request<{ items: Device[] }>('/devices'),
    );
  },

  putDeviceConfig(deviceId: string, config: DeviceConfig): Promise<DeviceConfigSaved> {
    return demoOrLive(
      () => demoApi.putDeviceConfig(deviceId, config),
      () =>
        request<DeviceConfigSaved>(`/devices/${encodeURIComponent(deviceId)}/config`, {
          method: 'PUT',
          body: config,
          timeoutMs: 10_000,
        }),
    );
  },

  // -------------------------------------------------------------------------
  // 搜索
  // -------------------------------------------------------------------------
  search(query: string, provider?: SearchProvider, limit = 5): Promise<SearchResponse> {
    return demoOrLive(
      () => demoApi.search(query, provider),
      () =>
        request<SearchResponse>('/search', {
          method: 'POST',
          body: { query, provider, limit },
        }),
    );
  },
};

/** 生成一个极简静音 WAV，供演示模式下的「下载音频」按钮使用。 */
function makeDemoWav(durationSeconds = 1): Blob {
  const sampleRate = 8000;
  const samples = Math.floor(sampleRate * durationSeconds);
  const buffer = new ArrayBuffer(44 + samples * 2);
  const view = new DataView(buffer);
  const writeString = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
  };
  writeString(0, 'RIFF');
  view.setUint32(4, 36 + samples * 2, true);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, 'data');
  view.setUint32(40, samples * 2, true);
  return new Blob([buffer], { type: 'audio/wav' });
}

function triggerDownload(blob: Blob, filename: string) {
  if (typeof document === 'undefined') return;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = 'noopener';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/**
 * 下载原始录音。必须带 API Key（Server 的 /audio/{segment_id}/file 也需要认证）。
 */
export async function downloadAudio(audioUrl: string, filename = 'audio.wav'): Promise<void> {
  if (isDemoMode()) {
    triggerDownload(makeDemoWav(), filename);
    return;
  }
  const config = loadConfig();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 60_000);
  try {
    const response = await fetch(absoluteAudioUrl(audioUrl, config), {
      headers: config.apiKey.trim() ? { 'X-API-Key': config.apiKey.trim() } : {},
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new ApiError(response.status, 'audio_download_failed', '录音下载失败，请稍后重试。');
    }
    const blob = await response.blob();
    triggerDownload(blob, filename);
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(0, 'audio_download_failed', '录音下载失败，请检查网络。');
  } finally {
    clearTimeout(timer);
  }
}

/** 导出文本（ASR 原文）。 */
export function downloadText(text: string, filename: string): void {
  triggerDownload(new Blob([text], { type: 'text/plain;charset=utf-8' }), filename);
}

export { todayIso };
