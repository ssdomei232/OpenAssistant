/**
 * 与 docs/api.md 契约严格一致的类型定义。
 * 字段名一律使用 snake_case，禁止在数据层做隐式改名。
 */

export type Speaker = 'user' | 'other' | 'unknown';
export type CardType = 'conversation' | 'meeting' | 'media' | 'other';
export type SearchProvider = 'builtin' | 'deepseek';
export type TodoStatus = 'pending' | 'done';
export type ItemSource = 'card' | 'command' | 'manual';
export type CommandIntent = 'search' | 'schedule_query' | 'memory_query' | 'todo' | 'none';
export type CommandStatus = 'done' | 'failed' | 'ignored';

export interface Paged<T> {
  items: T[];
  next_cursor: string | null;
}

export interface Health {
  status: string;
  version: string;
  mode: { asr: string; diarization: string; llm: string; search: string };
  time: string;
}

export interface VoiceprintState {
  enrolled: boolean;
  updated_at: string | null;
  threshold: number;
  quality: number | null;
  sample_seconds?: number | null;
}

export interface ProfileState {
  text: string;
  updated_at: string | null;
  version: number;
}

export interface Profile extends ProfileState {
  source_card_count?: number;
}

export interface Me {
  id: string;
  display_name: string;
  aliases: string[];
  timezone: string;
  search_provider: SearchProvider;
  voiceprint: VoiceprintState;
  profile: ProfileState;
  created_at: string;
}

export interface MeSettings {
  search_provider: SearchProvider;
  push_enabled: boolean;
  auto_schedule: boolean;
  auto_profile_update: boolean;
  correction_notice: boolean;
}

export interface CardLabels {
  user_turns: number;
  other_turns: number;
}

export interface CardSummary {
  id: string;
  title: string;
  summary: string;
  key_points: string[];
  people: string[];
  type: CardType;
  occurred_at: string;
  duration_s: number;
  audio_url: string | null;
  /** 合并对话可能对应多个音频片段（服务端新字段，旧数据可能没有） */
  audio_segment_ids?: string[];
  audio_urls?: string[];
  audio_available: boolean;
  labels: CardLabels;
  meeting_id: string | null;
  has_correction: boolean;
  deleted_at: string | null;
}

export interface TranscriptTurn {
  start: number;
  end: number;
  speaker: Speaker;
  speaker_label: string;
  text: string;
}

export interface ScheduleItem {
  id: string;
  title: string;
  start_at: string;
  end_at: string;
  location: string | null;
  description: string | null;
  source: ItemSource;
  card_id: string | null;
  status: string;
  remind_at: string | null;
  reminded: boolean;
  created_at: string;
}

export interface Todo {
  id: string;
  title: string;
  status: TodoStatus;
  due_at: string | null;
  source: ItemSource;
  card_id: string | null;
  priority: string;
  created_at: string;
  completed_at: string | null;
}

export interface CardScheduleRef {
  id: string;
  title: string;
  start_at: string;
  end_at: string;
  location: string | null;
  source: string;
}

export interface CardTodoRef {
  id: string;
  title: string;
  status: string;
  due_at: string | null;
}

export interface CardCommandRef {
  id: string;
  text: string;
  status: string;
  result: string | null;
  created_at: string;
}

export interface CardCorrection {
  id: string;
  at: string;
  field: string;
  before: unknown;
  after: unknown;
  note: string | null;
}

export interface CardDetail extends CardSummary {
  transcript: TranscriptTurn[];
  schedule_items: CardScheduleRef[];
  todos: CardTodoRef[];
  commands: CardCommandRef[];
  corrections: CardCorrection[];
  created_at: string;
  updated_at: string;
}

export interface CardPatch {
  title?: string;
  summary?: string;
  key_points?: string[];
  type?: CardType;
  note?: string;
}

export interface CardDeleteResult {
  deleted: boolean;
  cascaded: { schedule: number; todos: number; meetings: number };
  profile_dirty: boolean;
}

/** 手动合并多张卡片并重新总结的响应：返回合并后的主卡片详情。 */
export interface CardMergeResult extends CardDetail {
  merged_card_ids: string[];
  merge_reason: string;
}

export interface ScheduleCreate {
  title: string;
  start_at: string;
  end_at: string;
  location?: string;
  description?: string;
  remind_minutes_before?: number;
}

export type SchedulePatch = Partial<ScheduleCreate>;

export interface TodoCreate {
  title: string;
  due_at?: string;
  priority?: string;
}

export interface TodoPatch {
  title?: string;
  status?: TodoStatus;
  due_at?: string | null;
  priority?: string;
}

export interface MeetingParticipant {
  label: string;
  name: string;
}

export interface ActionItem {
  text: string;
  owner: string | null;
  due_at: string | null;
}

export interface Meeting {
  id: string;
  title: string;
  started_at: string;
  ended_at: string;
  participants: MeetingParticipant[];
  summary: string;
  action_items: ActionItem[];
  card_ids: string[];
  status: 'ready' | 'processing';
  audio_url: string | null;
  created_at: string;
}

export interface MeetingDetail extends Meeting {
  minutes?: string;
  decisions?: string[];
  topics?: string[];
}

export interface MeetingPatch {
  title?: string;
  summary?: string;
  minutes?: string;
  action_items?: ActionItem[];
}

export interface Command {
  id: string;
  text: string;
  intent: CommandIntent;
  status: CommandStatus;
  result: string | null;
  pushed: boolean;
  card_id: string | null;
  created_at: string;
}

export interface AuditEntry {
  id: string;
  at?: string;
  action: string;
  entity: string;
  entity_id: string;
  before?: unknown;
  after?: unknown;
  note?: string | null;
  [key: string]: unknown;
}

export interface DeviceConfig {
  wifi?: { ssid: string; password: string };
  retry_max?: number;
  offline_buffer?: boolean;
  upload_endpoint?: string;
  vad?: { aggressiveness: number; min_speech_ms: number };
}

export interface Device {
  id: string;
  name: string;
  last_seen: string;
  config: DeviceConfig;
  status: string;
}

export interface SearchResultItem {
  title: string;
  url: string;
  snippet: string;
}

export interface SearchResponse {
  query: string;
  provider: SearchProvider;
  answer: string;
  results: SearchResultItem[];
  took_ms: number;
}

export interface PushTestResponse {
  sent: boolean;
  provider: string;
  reason?: string;
}

export interface DeviceConfigSaved {
  saved: boolean;
  updated_at: string;
}

export interface DeleteResult {
  deleted: boolean;
}

export interface CardQuery {
  q?: string;
  from?: string;
  to?: string;
  type?: CardType;
  speaker?: Speaker;
  limit?: number;
  cursor?: string;
  include_deleted?: boolean;
}

export interface ScheduleQuery {
  from?: string;
  to?: string;
  include_deleted?: boolean;
}

export interface TodoQuery {
  status?: TodoStatus | 'all';
  limit?: number;
  cursor?: string;
}

export interface MeetingQuery {
  from?: string;
  to?: string;
  limit?: number;
  cursor?: string;
}

export interface CommandQuery {
  status?: CommandStatus;
  limit?: number;
  cursor?: string;
}

export interface AuditQuery {
  limit?: number;
  cursor?: string;
}
