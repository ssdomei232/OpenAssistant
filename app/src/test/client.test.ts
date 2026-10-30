import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api, isDemoMode, saveConfig } from '../api/client';
import { resetDemoData } from '../api/demo';
import type { CardSummary } from '../api/types';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const realCard: CardSummary = {
  id: 'card_real_1',
  title: '真实卡片',
  summary: '来自真实 Server 的摘要',
  key_points: [],
  people: [],
  type: 'conversation',
  occurred_at: '2026-03-05T09:30:00+08:00',
  duration_s: 12,
  audio_url: null,
  audio_available: false,
  labels: { user_turns: 2, other_turns: 1 },
  meeting_id: null,
  has_correction: false,
  deleted_at: null,
};

beforeEach(() => {
  resetDemoData();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

describe('API client 数据源切换', () => {
  it('未配置 API Key 时使用演示 fixtures，且不发起网络请求', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    expect(isDemoMode()).toBe(true);
    const cards = await api.getCards();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(cards.items.length).toBeGreaterThanOrEqual(3);
    expect(cards.items[0]).toHaveProperty('labels');
    expect(cards.items[0].title).toContain('Q3');
  });

  it('配置服务器与 API Key 后走真实网络，并携带 X-API-Key', async () => {
    saveConfig({
      serverUrl: 'https://oa.example.test/',
      apiKey: 'oa_test_key',
      demoMode: false,
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ items: [realCard], next_cursor: null }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await api.getCards({ q: '周会', limit: 5 });

    expect(result.items[0]).toEqual(realCard);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('https://oa.example.test/api/v1/cards?q=%E5%91%A8%E4%BC%9A&limit=5');
    expect((init.headers as Record<string, string>)['X-API-Key']).toBe('oa_test_key');
  });

  it('演示模式开关优先于已配置的 API Key', async () => {
    saveConfig({ serverUrl: 'https://oa.example.test', apiKey: 'oa_test_key', demoMode: true });
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    expect(isDemoMode()).toBe(true);
    const me = await api.getMe();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(me.aliases).toContain('小明');
  });

  it('401 会抛出带 code 与中文提示的 ApiError', async () => {
    saveConfig({ serverUrl: 'https://oa.example.test', apiKey: 'bad_key', demoMode: false });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ detail: 'invalid_api_key' }, 401)),
    );

    try {
      await api.getCards();
      expect.unreachable('应当抛出 ApiError');
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      const apiError = error as ApiError;
      expect(apiError.status).toBe(401);
      expect(apiError.detail).toBe('invalid_api_key');
      expect(apiError.message).toContain('API Key');
    }
  });

  it('演示模式的写操作在内存中生效（PATCH 待办）', async () => {
    const before = await api.getTodos({ status: 'pending' });
    const target = before.items.find((todo) => todo.status === 'pending');
    expect(target).toBeDefined();

    const updated = await api.updateTodo(target!.id, { status: 'done' });
    expect(updated.status).toBe('done');
    expect(updated.completed_at).not.toBeNull();

    const after = await api.getTodos({ status: 'pending' });
    expect(after.items.find((todo) => todo.id === target!.id)).toBeUndefined();
  });
});
