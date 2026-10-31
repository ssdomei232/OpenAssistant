/**
 * TanStack Query 数据层：统一 cache key、查询与 mutation（成功后 invalidate）。
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type QueryKey,
} from '@tanstack/react-query';
import { api } from './client';
import type {
  AuditQuery,
  CardPatch,
  CardQuery,
  CommandQuery,
  DeviceConfig,
  MeetingPatch,
  MeetingQuery,
  MeSettings,
  Paged,
  ScheduleCreate,
  SchedulePatch,
  Todo,
  TodoCreate,
  TodoPatch,
  TodoQuery,
} from './types';

export const queryKeys = {
  me: ['me'] as const,
  settings: ['settings'] as const,
  profile: ['profile'] as const,
  voiceprint: ['voiceprint'] as const,
  cards: (query: CardQuery) => ['cards', query] as const,
  card: (id: string) => ['card', id] as const,
  schedule: (from: string, to: string) => ['schedule', from, to] as const,
  allSchedule: ['schedule'] as const,
  todos: (status: string) => ['todos', status] as const,
  allTodos: ['todos'] as const,
  meetings: (query: MeetingQuery) => ['meetings', query] as const,
  meeting: (id: string) => ['meeting', id] as const,
  commands: (query: CommandQuery) => ['commands', query] as const,
  allCommands: ['commands'] as const,
  audit: (query: AuditQuery) => ['audit', query] as const,
  devices: ['devices'] as const,
};

function invalidate(client: QueryClient, keys: QueryKey[]) {
  for (const key of keys) void client.invalidateQueries({ queryKey: key });
}

// ---------------------------------------------------------------------------
// 账号 / 设置 / 画像 / 声纹
// ---------------------------------------------------------------------------
export function useMe() {
  return useQuery({ queryKey: queryKeys.me, queryFn: () => api.getMe() });
}

export function useUpdateMe() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (patch: Parameters<typeof api.updateMe>[0]) => api.updateMe(patch),
    onSuccess: () => invalidate(client, [queryKeys.me, queryKeys.settings]),
  });
}

export function useSettings() {
  return useQuery({ queryKey: queryKeys.settings, queryFn: () => api.getSettings() });
}

export function useUpdateSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (patch: Partial<MeSettings>) => api.updateSettings(patch),
    onSuccess: () => invalidate(client, [queryKeys.settings, queryKeys.me]),
  });
}

export function useProfile() {
  return useQuery({ queryKey: queryKeys.profile, queryFn: () => api.getProfile() });
}

export function useUpdateProfile() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (text: string) => api.updateProfile(text),
    onSuccess: () => invalidate(client, [queryKeys.profile, queryKeys.me]),
  });
}

export function useRefreshProfile() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.refreshProfile(),
    onSuccess: () => invalidate(client, [queryKeys.profile, queryKeys.me]),
  });
}

export function useVoiceprint() {
  return useQuery({ queryKey: queryKeys.voiceprint, queryFn: () => api.getVoiceprint() });
}

export function useUploadVoiceprint() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ file, threshold }: { file: File; threshold?: number }) =>
      api.uploadVoiceprint(file, threshold),
    onSuccess: () => invalidate(client, [queryKeys.voiceprint, queryKeys.me]),
  });
}

export function useDeleteVoiceprint() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.deleteVoiceprint(),
    onSuccess: () => invalidate(client, [queryKeys.voiceprint, queryKeys.me]),
  });
}

// ---------------------------------------------------------------------------
// 待办
// ---------------------------------------------------------------------------
export function useTodos(query: TodoQuery = { status: 'all' }) {
  return useQuery({
    queryKey: queryKeys.todos(query.status ?? 'all'),
    queryFn: () => api.getTodos(query),
  });
}

export function useCreateTodo() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: TodoCreate) => api.createTodo(payload),
    onSuccess: () => invalidate(client, [queryKeys.allTodos, queryKeys.cards({})]),
  });
}

export function useUpdateTodo() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: TodoPatch }) => api.updateTodo(id, patch),
    onMutate: async ({ id, patch }) => {
      await client.cancelQueries({ queryKey: queryKeys.allTodos });
      const snapshots = client.getQueriesData<Paged<Todo>>({ queryKey: queryKeys.allTodos });
      client.setQueriesData<Paged<Todo>>({ queryKey: queryKeys.allTodos }, (old) => {
        if (!old) return old;
        return {
          ...old,
          items: old.items.map((todo) =>
            todo.id === id
              ? {
                  ...todo,
                  ...patch,
                  completed_at:
                    patch.status === 'done'
                      ? new Date().toISOString()
                      : patch.status === 'pending'
                        ? null
                        : todo.completed_at,
                }
              : todo,
          ),
        };
      });
      return { snapshots };
    },
    onError: (_error, _variables, context) => {
      context?.snapshots.forEach(([key, data]) => client.setQueryData(key, data));
    },
    onSettled: () => {
      invalidate(client, [queryKeys.allTodos, queryKeys.cards({})]);
    },
  });
}

export function useDeleteTodo() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteTodo(id),
    onSuccess: () => invalidate(client, [queryKeys.allTodos, queryKeys.cards({})]),
  });
}

// ---------------------------------------------------------------------------
// 日程
// ---------------------------------------------------------------------------
export function useSchedule(from: string, to: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.schedule(from, to),
    queryFn: () => api.getSchedule({ from, to }),
    enabled,
  });
}

export function useCreateSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: ScheduleCreate) => api.createSchedule(payload),
    onSuccess: () => invalidate(client, [queryKeys.allSchedule]),
  });
}

export function useUpdateSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: SchedulePatch }) =>
      api.updateSchedule(id, patch),
    onSuccess: () => invalidate(client, [queryKeys.allSchedule]),
  });
}

export function useDeleteSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteSchedule(id),
    onSuccess: () => invalidate(client, [queryKeys.allSchedule]),
  });
}

// ---------------------------------------------------------------------------
// 卡片
// ---------------------------------------------------------------------------
export function useCards(query: CardQuery) {
  return useQuery({
    queryKey: queryKeys.cards(query),
    queryFn: () => api.getCards(query),
  });
}

export function useCard(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.card(id ?? ''),
    queryFn: () => api.getCard(id as string),
    enabled: Boolean(id),
  });
}

export function useUpdateCard() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: CardPatch }) => api.updateCard(id, patch),
    onSuccess: (_data, { id }) => {
      invalidate(client, [queryKeys.card(id), ['cards'], queryKeys.allSchedule, queryKeys.allTodos]);
    },
  });
}

export function useDeleteCard() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteCard(id),
    onSuccess: () => {
      invalidate(client, [
        ['cards'],
        queryKeys.allSchedule,
        queryKeys.allTodos,
        ['meetings'],
        queryKeys.profile,
        ['audit'],
      ]);
    },
  });
}

export function useMergeCards() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ cardIds, note }: { cardIds: string[]; note?: string }) =>
      api.mergeCards(cardIds, note),
    onSuccess: () => {
      invalidate(client, [
        ['cards'],
        ['card'],
        queryKeys.allSchedule,
        queryKeys.allTodos,
        ['meetings'],
        queryKeys.profile,
        ['audit'],
      ]);
    },
  });
}

// ---------------------------------------------------------------------------
// 会议
// ---------------------------------------------------------------------------
export function useMeetings(query: MeetingQuery = {}) {
  return useQuery({ queryKey: queryKeys.meetings(query), queryFn: () => api.getMeetings(query) });
}

export function useMeeting(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.meeting(id ?? ''),
    queryFn: () => api.getMeeting(id as string),
    enabled: Boolean(id),
  });
}

export function useRegenerateMeeting() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.regenerateMeeting(id),
    onSuccess: (_data, id) => {
      invalidate(client, [queryKeys.meeting(id), ['meetings']]);
    },
  });
}

export function useUpdateMeeting() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: MeetingPatch }) =>
      api.updateMeeting(id, patch),
    onSuccess: (_data, { id }) => {
      invalidate(client, [queryKeys.meeting(id), ['meetings']]);
    },
  });
}

export function useDeleteMeeting() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteMeeting(id),
    onSuccess: () => invalidate(client, [['meetings']]),
  });
}

// ---------------------------------------------------------------------------
// 指令 / 审计 / 设备 / 推送
// ---------------------------------------------------------------------------
export function useCommands(query: CommandQuery = {}) {
  return useQuery({
    queryKey: queryKeys.commands(query),
    queryFn: () => api.getCommands(query),
  });
}

export function useRetryCommand() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.retryCommand(id),
    onSuccess: () => invalidate(client, [queryKeys.allCommands]),
  });
}

export function useAudit(query: AuditQuery = {}) {
  return useQuery({ queryKey: queryKeys.audit(query), queryFn: () => api.getAudit(query) });
}

export function useDevices() {
  return useQuery({ queryKey: queryKeys.devices, queryFn: () => api.getDevices() });
}

export function usePutDeviceConfig() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ deviceId, config }: { deviceId: string; config: DeviceConfig }) =>
      api.putDeviceConfig(deviceId, config),
    onSuccess: () => invalidate(client, [queryKeys.devices]),
  });
}

export function usePushTest() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (text?: string) => api.pushTest(text),
    onSuccess: () => invalidate(client, [['audit']]),
  });
}
