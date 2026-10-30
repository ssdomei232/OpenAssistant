/**
 * 演示模式（Demo mode）内置 fixtures 与内存写操作。
 *
 * 触发条件：settings 中未配置 API Key，或用户显式打开「演示模式」。
 * 所有数据均为中文虚构示例，绝不含真实用户数据。
 */
import type {
  AuditEntry,
  AuditQuery,
  CardDeleteResult,
  CardDetail,
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
  TranscriptTurn,
  VoiceprintState,
} from './types';

const delay = (ms = 60) => new Promise((resolve) => setTimeout(resolve, ms));
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
const nowIso = () => new Date().toISOString();

/** 以本地时区构造「相对今天 offset 天、hour:minute」的 ISO 时间。 */
function atDay(offset: number, hour: number, minute = 0): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  d.setHours(hour, minute, 0, 0);
  return d.toISOString();
}

function turn(
  start: number,
  end: number,
  speaker: 'user' | 'other' | 'unknown',
  text: string,
): TranscriptTurn {
  const label =
    speaker === 'user' ? '[使用者]' : speaker === 'other' ? '[其他人]' : '[未知]';
  return { start, end, speaker, speaker_label: label, text };
}

// ---------------------------------------------------------------------------
// 用户与画像
// ---------------------------------------------------------------------------

const meFixture: Me = {
  id: 'usr_demo_01',
  display_name: '小明',
  aliases: ['小明', '明明', 'Xiao Ming'],
  timezone: 'Asia/Shanghai',
  search_provider: 'builtin',
  voiceprint: {
    enrolled: true,
    updated_at: atDay(-12, 10, 20),
    threshold: 60,
    quality: 0.82,
  },
  profile: {
    text: '30 岁软件工程师，常住上海；关注 AI 与开源；偏好简短直接的沟通；周三晚上有羽毛球活动。',
    updated_at: atDay(-1, 21, 5),
    version: 3,
  },
  created_at: atDay(-46, 9, 0),
};

let profileFixture: Profile = {
  text: '30 岁软件工程师，常住上海；关注 AI 与开源；偏好简短直接的沟通；周三晚上有羽毛球活动。最近在推进 Q3 交付计划，常与张伟、李娜协作。',
  version: 3,
  updated_at: atDay(-1, 21, 5),
  source_card_count: 12,
};

let settingsFixture: MeSettings = {
  search_provider: 'builtin',
  push_enabled: true,
  auto_schedule: true,
  auto_profile_update: true,
  correction_notice: true,
};

let voiceprintFixture: VoiceprintState = { ...meFixture.voiceprint };

// ---------------------------------------------------------------------------
// 日程 / 待办
// ---------------------------------------------------------------------------

const scheduleFixture: ScheduleItem[] = [
  {
    id: 'evt_demo_standup',
    title: '团队晨会',
    start_at: atDay(0, 9, 0),
    end_at: atDay(0, 9, 15),
    location: '线上',
    description: '同步今日进展与阻塞项',
    source: 'manual',
    card_id: null,
    status: 'confirmed',
    remind_at: atDay(0, 8, 50),
    reminded: true,
    created_at: atDay(-3, 18, 0),
  },
  {
    id: 'evt_demo_meet',
    title: '和周磊开会',
    start_at: atDay(0, 15, 0),
    end_at: atDay(0, 16, 0),
    location: '3 号会议室',
    description: '对齐 Q3 排期与接口联调计划',
    source: 'card',
    card_id: 'card_demo_q3',
    status: 'confirmed',
    remind_at: atDay(0, 14, 30),
    reminded: false,
    created_at: atDay(0, 9, 32),
  },
  {
    id: 'evt_demo_gym',
    title: '健身房训练',
    start_at: atDay(0, 19, 30),
    end_at: atDay(0, 20, 30),
    location: '公司楼下健身房',
    description: null,
    source: 'command',
    card_id: null,
    status: 'confirmed',
    remind_at: atDay(0, 19, 0),
    reminded: false,
    created_at: atDay(-1, 8, 0),
  },
  {
    id: 'evt_demo_badminton',
    title: '羽毛球活动',
    start_at: atDay(2, 19, 0),
    end_at: atDay(2, 21, 0),
    location: '浦东体育馆 2 号场地',
    description: '记得带球拍',
    source: 'card',
    card_id: 'card_demo_badminton',
    status: 'confirmed',
    remind_at: atDay(2, 18, 30),
    reminded: false,
    created_at: atDay(-1, 18, 25),
  },
  {
    id: 'evt_demo_client',
    title: '客户回访电话',
    start_at: atDay(3, 10, 30),
    end_at: atDay(3, 11, 0),
    location: '线上会议',
    description: '确认交付时间与验收标准',
    source: 'manual',
    card_id: null,
    status: 'confirmed',
    remind_at: atDay(3, 10, 0),
    reminded: false,
    created_at: atDay(-2, 16, 0),
  },
  {
    id: 'evt_demo_past_weekly',
    title: '项目周会',
    start_at: atDay(-2, 14, 0),
    end_at: atDay(-2, 15, 0),
    location: '5 号会议室',
    description: 'Q3 排期评审',
    source: 'card',
    card_id: 'card_demo_meeting',
    status: 'confirmed',
    remind_at: null,
    reminded: true,
    created_at: atDay(-2, 14, 5),
  },
  {
    id: 'evt_demo_dentist',
    title: '牙医复诊',
    start_at: atDay(-6, 11, 0),
    end_at: atDay(-6, 11, 30),
    location: '静安口腔医院',
    description: null,
    source: 'manual',
    card_id: null,
    status: 'confirmed',
    remind_at: null,
    reminded: true,
    created_at: atDay(-10, 9, 0),
  },
];

const todoFixture: Todo[] = [
  {
    id: 'todo_demo_q3',
    title: '给张伟发 Q3 排期表',
    status: 'pending',
    due_at: atDay(0, 18, 0),
    source: 'card',
    card_id: 'card_demo_q3',
    priority: 'high',
    created_at: atDay(0, 9, 33),
    completed_at: null,
  },
  {
    id: 'todo_demo_feedback',
    title: '整理客户反馈清单',
    status: 'pending',
    due_at: atDay(1, 12, 0),
    source: 'card',
    card_id: 'card_demo_meeting',
    priority: 'normal',
    created_at: atDay(-2, 14, 40),
    completed_at: null,
  },
  {
    id: 'todo_demo_milk',
    title: '下班路上买牛奶',
    status: 'pending',
    due_at: atDay(0, 20, 0),
    source: 'manual',
    card_id: null,
    priority: 'low',
    created_at: atDay(0, 8, 15),
    completed_at: null,
  },
  {
    id: 'todo_demo_racket',
    title: '把羽毛球拍放进包里',
    status: 'pending',
    due_at: atDay(2, 18, 0),
    source: 'card',
    card_id: 'card_demo_badminton',
    priority: 'normal',
    created_at: atDay(-1, 18, 26),
    completed_at: null,
  },
  {
    id: 'todo_demo_checkup',
    title: '预约年度体检',
    status: 'done',
    due_at: atDay(-1, 10, 0),
    source: 'manual',
    card_id: null,
    priority: 'normal',
    created_at: atDay(-4, 9, 0),
    completed_at: atDay(-1, 10, 12),
  },
];

// ---------------------------------------------------------------------------
// 会议
// ---------------------------------------------------------------------------

const meetingsFixture: MeetingDetail[] = [
  {
    id: 'mtg_demo_q3',
    title: 'Q3 排期评审',
    started_at: atDay(-2, 14, 0),
    ended_at: atDay(-2, 15, 0),
    participants: [
      { label: '[使用者]', name: '小明' },
      { label: '[其他人]', name: '张伟' },
      { label: '[其他人]', name: '李娜' },
    ],
    summary:
      '评审 Q3 交付排期：上季度延期两周，客户要求提前对齐；决定将设计评审提前到 6 月 20 日，张伟负责排期表与风险清单。',
    action_items: [
      { text: '输出 Q3 排期表并发给客户', owner: '张伟', due_at: atDay(1, 12, 0) },
      { text: '整理上季度延期复盘', owner: '李娜', due_at: atDay(2, 18, 0) },
      { text: '确认设计评审时间与参与人', owner: '小明', due_at: atDay(0, 18, 0) },
    ],
    card_ids: ['card_demo_meeting'],
    status: 'ready',
    audio_url: '/api/v1/audio/seg_demo_003/file',
    created_at: atDay(-2, 15, 5),
    minutes: `# Q3 排期评审纪要

## 议题
1. 上季度交付延期复盘
2. Q3 里程碑与设计评审时间
3. 客户沟通节奏

## 决议
- 设计评审提前到 **6 月 20 日**，由小明确认参与人
- Q3 排期表由张伟在周五前发出
- 客户沟通改为每两周一次

## 行动项
- [ ] 输出 Q3 排期表并发给客户（张伟）
- [ ] 整理上季度延期复盘（李娜）
- [ ] 确认设计评审时间与参与人（小明）

> 下次同步时间：下周一 10:00。`,
    decisions: ['设计评审提前到 6 月 20 日', 'Q3 排期表周五前发出', '客户沟通改为每两周一次'],
    topics: ['交付延期复盘', 'Q3 里程碑', '客户沟通节奏'],
  },
  {
    id: 'mtg_demo_weekly',
    title: '客户反馈复盘会',
    started_at: atDay(-7, 10, 0),
    ended_at: atDay(-7, 10, 40),
    participants: [
      { label: '[使用者]', name: '小明' },
      { label: '[其他人]', name: '王芳' },
    ],
    summary: '复盘近两周客户反馈：集中在导出速度与移动端适配，决定下个迭代优先处理导出性能。',
    action_items: [{ text: '给出导出性能优化方案', owner: '小明', due_at: atDay(-3, 18, 0) }],
    card_ids: [],
    status: 'ready',
    audio_url: null,
    created_at: atDay(-7, 10, 45),
    minutes: `# 客户反馈复盘会

## 议题
- 近两周工单分类回顾
- 移动端适配问题

## 决议
- 下个迭代优先优化导出性能
- 移动端问题统一收集后再排期

## 行动项
- [ ] 给出导出性能优化方案（小明）`,
    decisions: ['下个迭代优先优化导出性能'],
    topics: ['客户工单回顾', '移动端适配'],
  },
];

// ---------------------------------------------------------------------------
// 指令与审计
// ---------------------------------------------------------------------------

const commandsFixture: Command[] = [
  {
    id: 'cmd_demo_weather',
    text: '帮我查一下明天上海的天气',
    intent: 'search',
    status: 'done',
    result: '上海明天多云转晴，18~25℃，东南风 3 级，适合户外活动。',
    pushed: true,
    card_id: null,
    created_at: atDay(0, 8, 10),
  },
  {
    id: 'cmd_demo_schedule',
    text: '帮我记一下明天下午三点和周磊开会',
    intent: 'schedule_query',
    status: 'done',
    result: '已添加日程：明天 15:00「和周磊开会」，地点 3 号会议室，并设置提前 30 分钟提醒。',
    pushed: true,
    card_id: 'card_demo_q3',
    created_at: atDay(0, 9, 32),
  },
  {
    id: 'cmd_demo_memory',
    text: '我上次和周磊聊了什么？',
    intent: 'memory_query',
    status: 'done',
    result: '最近一次与周磊的对话在 3 天前，提到 Q3 排期、接口联调与羽毛球活动。',
    pushed: true,
    card_id: 'card_demo_q3',
    created_at: atDay(-1, 20, 15),
  },
  {
    id: 'cmd_demo_failed',
    text: '帮我查一下去杭州的高铁时刻',
    intent: 'search',
    status: 'failed',
    result: '钉钉推送失败：webhook 请求超时（已进入重试队列）。',
    pushed: false,
    card_id: null,
    created_at: atDay(-2, 7, 40),
  },
];

const auditFixture: AuditEntry[] = [
  {
    id: 'audit_demo_01',
    at: atDay(-2, 16, 20),
    action: 'card.correct',
    entity: 'card',
    entity_id: 'card_demo_media',
    before: { type: 'conversation' },
    after: { type: 'media' },
    note: '其实是电视节目声音',
  },
  {
    id: 'audit_demo_02',
    at: atDay(-3, 9, 5),
    action: 'voiceprint.enroll',
    entity: 'voiceprint',
    entity_id: 'vp_demo',
    before: null,
    after: { threshold: 60, quality: 0.82 },
    note: '声纹注册成功',
  },
  {
    id: 'audit_demo_03',
    at: atDay(-4, 19, 30),
    action: 'todo.delete',
    entity: 'todo',
    entity_id: 'todo_demo_old',
    before: { title: '重复的待办' },
    after: { deleted_at: atDay(-4, 19, 30) },
    note: '手动删除',
  },
  {
    id: 'audit_demo_04',
    at: atDay(-5, 11, 0),
    action: 'device.config',
    entity: 'device',
    entity_id: 'pi-livingroom',
    before: { retry_max: 3 },
    after: { retry_max: 5 },
    note: 'App 通过蓝牙写入并备份',
  },
];

// ---------------------------------------------------------------------------
// 卡片
// ---------------------------------------------------------------------------

const cardsFixture: CardDetail[] = [
  {
    id: 'card_demo_q3',
    title: '和产品经理讨论 Q3 排期',
    summary:
      '与张伟、李娜确认 Q3 交付排期：上季度延期两周，客户希望提前对齐；决定把设计评审提前到 6 月 20 日，张伟周五前发出排期表，并约周磊明天下午三点在三号会议室对齐接口。',
    key_points: [
      'Q3 目标：9 月底前完成核心功能',
      '设计评审提前到 6 月 20 日',
      '张伟周五前发出 Q3 排期表',
      '明天 15:00 与周磊在 3 号会议室开会',
    ],
    people: ['张伟', '李娜', '周磊'],
    type: 'conversation',
    occurred_at: atDay(0, 9, 30),
    duration_s: 42.5,
    audio_url: '/api/v1/audio/seg_demo_001/file',
    audio_available: true,
    labels: { user_turns: 2, other_turns: 2 },
    meeting_id: null,
    has_correction: false,
    deleted_at: null,
    transcript: [
      turn(0.4, 6.2, 'user', '帮我记一下，明天下午三点和周磊开会，地点在三号会议室。'),
      turn(6.5, 9.8, 'other', '好的，你们在哪个会议室？'),
      turn(10.1, 14.0, 'user', '三号会议室。另外提醒我周五前给张伟发 Q3 排期表。'),
      turn(14.3, 18.9, 'other', '收到，我这边先整理排期和风险清单。'),
    ],
    schedule_items: [
      {
        id: 'evt_demo_meet',
        title: '和周磊开会',
        start_at: atDay(0, 15, 0),
        end_at: atDay(0, 16, 0),
        location: '3 号会议室',
        source: 'card',
      },
    ],
    todos: [
      {
        id: 'todo_demo_q3',
        title: '给张伟发 Q3 排期表',
        status: 'pending',
        due_at: atDay(0, 18, 0),
      },
    ],
    commands: [
      {
        id: 'cmd_demo_schedule',
        text: '帮我记一下明天下午三点和周磊开会',
        status: 'done',
        result: '已添加日程：明天 15:00「和周磊开会」',
        created_at: atDay(0, 9, 32),
      },
    ],
    corrections: [],
    created_at: atDay(0, 9, 34),
    updated_at: atDay(0, 9, 34),
  },
  {
    id: 'card_demo_badminton',
    title: '约周三晚上打羽毛球',
    summary:
      '和陈晨约好周三晚上七点在浦东体育馆打球，需要提前订 2 号场地，并记得带球拍和护膝。',
    key_points: ['周三 19:00 浦东体育馆 2 号场地', '记得带球拍和护膝'],
    people: ['陈晨'],
    type: 'conversation',
    occurred_at: atDay(-1, 18, 20),
    duration_s: 28.4,
    audio_url: '/api/v1/audio/seg_demo_002/file',
    audio_available: true,
    labels: { user_turns: 1, other_turns: 1 },
    meeting_id: null,
    has_correction: false,
    deleted_at: null,
    transcript: [
      turn(0.2, 8.1, 'user', '周三晚上七点还是老地方打球，帮我记一下，记得带球拍。'),
      turn(8.4, 12.6, 'other', '好呀，我下班直接过去，你记得订场地。'),
    ],
    schedule_items: [
      {
        id: 'evt_demo_badminton',
        title: '羽毛球活动',
        start_at: atDay(2, 19, 0),
        end_at: atDay(2, 21, 0),
        location: '浦东体育馆 2 号场地',
        source: 'card',
      },
    ],
    todos: [
      {
        id: 'todo_demo_racket',
        title: '把羽毛球拍放进包里',
        status: 'pending',
        due_at: atDay(2, 18, 0),
      },
    ],
    commands: [],
    corrections: [],
    created_at: atDay(-1, 18, 27),
    updated_at: atDay(-1, 18, 27),
  },
  {
    id: 'card_demo_meeting',
    title: '项目周会：Q3 排期评审',
    summary:
      'Q3 排期评审会：上季度交付延期两周，客户要求提前对齐。会议决定设计评审提前到 6 月 20 日，张伟负责排期表，李娜负责延期复盘。',
    key_points: [
      '上季度交付延期两周',
      '设计评审提前到 6 月 20 日',
      '张伟负责 Q3 排期表',
      '客户沟通改为每两周一次',
    ],
    people: ['张伟', '李娜'],
    type: 'meeting',
    occurred_at: atDay(-2, 14, 0),
    duration_s: 3480,
    audio_url: '/api/v1/audio/seg_demo_003/file',
    audio_available: true,
    labels: { user_turns: 2, other_turns: 2 },
    meeting_id: 'mtg_demo_q3',
    has_correction: false,
    deleted_at: null,
    transcript: [
      turn(0.8, 12.4, 'user', '我们开始 Q3 排期评审，先过一下客户反馈和上季度延期的问题。'),
      turn(12.9, 38.2, 'other', '上季度交付延期了两周，客户希望这次能提前对齐里程碑。'),
      turn(38.6, 55.1, 'user', '那我们把设计评审提前到 6 月 20 日，张伟负责排期表，周五前发出。'),
      turn(55.6, 72.0, 'other', '可以，我周五前给出风险清单，李娜整理延期复盘。'),
    ],
    schedule_items: [
      {
        id: 'evt_demo_past_weekly',
        title: '项目周会',
        start_at: atDay(-2, 14, 0),
        end_at: atDay(-2, 15, 0),
        location: '5 号会议室',
        source: 'card',
      },
    ],
    todos: [
      {
        id: 'todo_demo_feedback',
        title: '整理客户反馈清单',
        status: 'pending',
        due_at: atDay(1, 12, 0),
      },
    ],
    commands: [],
    corrections: [],
    created_at: atDay(-2, 15, 6),
    updated_at: atDay(-2, 15, 6),
  },
  {
    id: 'card_demo_media',
    title: '疑似电视节目声音（已勘误）',
    summary:
      '该片段被识别为对话，实际是客厅电视播放的科技节目音频。已标记为流媒体/视频声，不计入日程与画像。',
    key_points: ['来源为电视节目，不是本人对话', '已标记为 media，不触发指令'],
    people: [],
    type: 'media',
    occurred_at: atDay(-3, 20, 10),
    duration_s: 96.2,
    audio_url: '/api/v1/audio/seg_demo_004/file',
    audio_available: true,
    labels: { user_turns: 1, other_turns: 1 },
    meeting_id: null,
    has_correction: true,
    deleted_at: null,
    transcript: [
      turn(0.5, 18.2, 'user', '欢迎收看本期科技观察，今天我们聊聊端侧 AI 的新进展。'),
      turn(18.6, 40.0, 'other', '这看起来像节目主持人的开场白，不像日常对话。'),
    ],
    schedule_items: [],
    todos: [],
    commands: [],
    corrections: [
      {
        id: 'corr_demo_01',
        at: atDay(-2, 16, 20),
        field: 'type',
        before: 'conversation',
        after: 'media',
        note: '其实是电视节目声音',
      },
      {
        id: 'corr_demo_02',
        at: atDay(-2, 16, 22),
        field: 'summary',
        before: '讨论端侧 AI 的新进展。',
        after: '该片段为客厅电视播放的科技节目音频，已标记为媒体声。',
        note: '同步修正摘要',
      },
    ],
    created_at: atDay(-3, 20, 12),
    updated_at: atDay(-2, 16, 22),
  },
];

// ---------------------------------------------------------------------------
// Demo API 实现（与 client.ts 方法一一对应）
// ---------------------------------------------------------------------------

/** 初始快照，供测试在用例之间重置演示数据。 */
const initialSnapshot = {
  cards: clone(cardsFixture),
  todos: clone(todoFixture),
  schedule: clone(scheduleFixture),
  meetings: clone(meetingsFixture),
  commands: clone(commandsFixture),
  audit: clone(auditFixture),
  profile: clone(profileFixture),
  settings: clone(settingsFixture),
  voiceprint: clone(voiceprintFixture),
  me: clone(meFixture),
};

export function resetDemoData(): void {
  cardsFixture.splice(0, cardsFixture.length, ...clone(initialSnapshot.cards));
  todoFixture.splice(0, todoFixture.length, ...clone(initialSnapshot.todos));
  scheduleFixture.splice(0, scheduleFixture.length, ...clone(initialSnapshot.schedule));
  meetingsFixture.splice(0, meetingsFixture.length, ...clone(initialSnapshot.meetings));
  commandsFixture.splice(0, commandsFixture.length, ...clone(initialSnapshot.commands));
  auditFixture.splice(0, auditFixture.length, ...clone(initialSnapshot.audit));
  profileFixture = clone(initialSnapshot.profile);
  settingsFixture = clone(initialSnapshot.settings);
  voiceprintFixture = clone(initialSnapshot.voiceprint);
  const meSnapshot = clone(initialSnapshot.me);
  Object.assign(meFixture, meSnapshot);
  meFixture.aliases = [...meSnapshot.aliases];
}

export interface DemoVoiceprintUpload {
  enrolled: boolean;
  threshold: number;
  quality: number;
  duration_s: number;
  updated_at: string;
}

function paginate<T>(items: T[], limit = 20): Paged<T> {
  return { items: clone(items.slice(0, limit)), next_cursor: null };
}

function matchesCard(card: CardDetail, query: CardQuery): boolean {
  if (!query.include_deleted && card.deleted_at) return false;
  if (query.type && card.type !== query.type) return false;
  if (query.from && card.occurred_at < query.from) return false;
  if (query.to && card.occurred_at > query.to) return false;
  if (query.speaker === 'user' && card.labels.user_turns === 0) return false;
  if (query.speaker === 'other' && card.labels.other_turns === 0) return false;
  if (query.q) {
    const q = query.q.trim().toLowerCase();
    if (q) {
      const haystack = [card.title, card.summary, ...card.key_points, ...card.people]
        .join('\n')
        .toLowerCase();
      if (!haystack.includes(q)) return false;
    }
  }
  return true;
}

function summaryOf(card: CardDetail): CardSummary {
  return {
    id: card.id,
    title: card.title,
    summary: card.summary,
    key_points: card.key_points,
    people: card.people,
    type: card.type,
    occurred_at: card.occurred_at,
    duration_s: card.duration_s,
    audio_url: card.audio_url,
    audio_available: card.audio_available,
    labels: card.labels,
    meeting_id: card.meeting_id,
    has_correction: card.corrections.length > 0,
    deleted_at: card.deleted_at,
  };
}

const devicesFixture: Device[] = [
  {
    id: 'pi-livingroom',
    name: '客厅树莓派',
    last_seen: new Date(Date.now() - 2 * 60_000).toISOString(),
    config: {
      wifi: { ssid: 'home', password: '******' },
      retry_max: 5,
      offline_buffer: true,
      upload_endpoint: 'https://oa.example.com/api/v1',
      vad: { aggressiveness: 2, min_speech_ms: 300 },
    },
    status: 'online',
  },
];

export const demoApi = {
  async getHealth(): Promise<Health> {
    await delay();
    return {
      status: 'ok',
      version: '1.0.0-demo',
      mode: { asr: 'mock', diarization: 'energy', llm: 'rule', search: 'builtin' },
      time: nowIso(),
    };
  },

  async getMe(): Promise<Me> {
    await delay();
    return clone({
      ...meFixture,
      aliases: [...meFixture.aliases],
      voiceprint: voiceprintFixture,
      search_provider: settingsFixture.search_provider,
      profile: {
        text: profileFixture.text,
        updated_at: profileFixture.updated_at,
        version: profileFixture.version,
      },
    });
  },

  async patchMe(patch: Partial<Pick<Me, 'display_name' | 'aliases' | 'timezone' | 'search_provider'>>): Promise<Me> {
    await delay();
    if (patch.display_name !== undefined) meFixture.display_name = patch.display_name;
    if (patch.aliases !== undefined) meFixture.aliases = [...patch.aliases];
    if (patch.timezone !== undefined) meFixture.timezone = patch.timezone;
    if (patch.search_provider !== undefined) {
      meFixture.search_provider = patch.search_provider;
      settingsFixture.search_provider = patch.search_provider;
    }
    return demoApi.getMe();
  },

  async getSettings(): Promise<MeSettings> {
    await delay();
    return clone(settingsFixture);
  },

  async patchSettings(patch: Partial<MeSettings>): Promise<MeSettings> {
    await delay();
    settingsFixture = { ...settingsFixture, ...patch };
    if (patch.search_provider) meFixture.search_provider = patch.search_provider;
    return clone(settingsFixture);
  },

  async getProfile(): Promise<Profile> {
    await delay();
    return clone(profileFixture);
  },

  async putProfile(text: string): Promise<Profile> {
    await delay();
    if (text.length > 600) throw new Error('profile_too_long');
    profileFixture = {
      ...profileFixture,
      text,
      version: profileFixture.version + 1,
      updated_at: nowIso(),
    };
    return clone(profileFixture);
  },

  async refreshProfile(): Promise<Profile> {
    await delay(220);
    profileFixture = {
      ...profileFixture,
      text: '30 岁软件工程师，常住上海；关注 AI 与开源；偏好简短直接的沟通；正在推进 Q3 交付，常与张伟、李娜协作；周三晚上打羽毛球。',
      version: profileFixture.version + 1,
      updated_at: nowIso(),
      source_card_count: cardsFixture.length,
    };
    return clone(profileFixture);
  },

  async getVoiceprint(): Promise<VoiceprintState> {
    await delay();
    return clone(voiceprintFixture);
  },

  async uploadVoiceprint(_file: File, threshold = 60): Promise<DemoVoiceprintUpload> {
    await delay(400);
    voiceprintFixture = {
      enrolled: true,
      threshold,
      quality: 0.8,
      updated_at: nowIso(),
    };
    return {
      enrolled: true,
      threshold,
      quality: 0.8,
      duration_s: 14.2,
      updated_at: nowIso(),
    };
  },

  async deleteVoiceprint(): Promise<DeleteResult> {
    await delay();
    voiceprintFixture = { enrolled: false, updated_at: null, threshold: 60, quality: null };
    return { deleted: true };
  },

  async getCards(query: CardQuery = {}): Promise<Paged<CardSummary>> {
    await delay();
    const items = cardsFixture
      .filter((card) => matchesCard(card, query))
      .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at))
      .map(summaryOf);
    return paginate(items, query.limit ?? 20);
  },

  async getCard(id: string): Promise<CardDetail> {
    await delay();
    const card = cardsFixture.find((c) => c.id === id);
    if (!card) throw new Error('card_not_found');
    return clone(card);
  },

  async patchCard(id: string, patch: CardPatch): Promise<CardDetail> {
    await delay();
    const card = cardsFixture.find((c) => c.id === id);
    if (!card) throw new Error('card_not_found');
    const fields = ['title', 'summary', 'key_points', 'type'] as const;
    for (const field of fields) {
      const next = patch[field];
      if (next === undefined) continue;
      const before = card[field];
      if (JSON.stringify(before) === JSON.stringify(next)) continue;
      card.corrections.push({
        id: `corr_${Date.now()}_${field}`,
        at: nowIso(),
        field,
        before,
        after: next,
        note: patch.note ?? null,
      });
      // 逐字段赋值，保持类型安全
      if (field === 'title') card.title = String(next);
      if (field === 'summary') card.summary = String(next);
      if (field === 'type') card.type = next as CardDetail['type'];
      if (field === 'key_points') card.key_points = next as string[];
    }
    card.has_correction = card.corrections.length > 0;
    card.updated_at = nowIso();
    return clone(card);
  },

  async deleteCard(id: string): Promise<CardDeleteResult> {
    await delay();
    const index = cardsFixture.findIndex((c) => c.id === id);
    if (index < 0) throw new Error('card_not_found');
    const [card] = cardsFixture.splice(index, 1);
    const scheduleIds = new Set(card.schedule_items.map((s) => s.id));
    const todoIds = new Set(card.todos.map((t) => t.id));
    let schedule = 0;
    let todos = 0;
    for (let i = scheduleFixture.length - 1; i >= 0; i -= 1) {
      const item = scheduleFixture[i];
      if (item.card_id === id || scheduleIds.has(item.id)) {
        scheduleFixture.splice(i, 1);
        schedule += 1;
      }
    }
    for (let i = todoFixture.length - 1; i >= 0; i -= 1) {
      const item = todoFixture[i];
      if (item.card_id === id || todoIds.has(item.id)) {
        todoFixture.splice(i, 1);
        todos += 1;
      }
    }
    const beforeMeetings = meetingsFixture.length;
    for (let i = meetingsFixture.length - 1; i >= 0; i -= 1) {
      if (meetingsFixture[i].card_ids.includes(id)) meetingsFixture.splice(i, 1);
    }
    return {
      deleted: true,
      cascaded: { schedule, todos, meetings: beforeMeetings - meetingsFixture.length },
      profile_dirty: true,
    };
  },

  async getTranscriptText(id: string): Promise<string> {
    await delay();
    const card = cardsFixture.find((c) => c.id === id);
    if (!card) throw new Error('card_not_found');
    return card.transcript
      .map((t) => `${t.speaker_label} ${formatClock(t.start)}-${formatClock(t.end)}\n${t.text}`)
      .join('\n\n');
  },

  async getSchedule(query: ScheduleQuery = {}): Promise<{ items: ScheduleItem[] }> {
    await delay();
    const items = scheduleFixture
      .filter((item) => {
        if (query.from && item.start_at < query.from) return false;
        if (query.to && item.start_at > query.to) return false;
        return true;
      })
      .sort((a, b) => a.start_at.localeCompare(b.start_at));
    return { items: clone(items) };
  },

  async createSchedule(payload: ScheduleCreate): Promise<ScheduleItem> {
    await delay();
    const item: ScheduleItem = {
      id: `evt_demo_${Date.now()}`,
      title: payload.title,
      start_at: payload.start_at,
      end_at: payload.end_at,
      location: payload.location ?? null,
      description: payload.description ?? null,
      source: 'manual',
      card_id: null,
      status: 'confirmed',
      remind_at:
        typeof payload.remind_minutes_before === 'number'
          ? new Date(
              new Date(payload.start_at).getTime() - payload.remind_minutes_before * 60_000,
            ).toISOString()
          : null,
      reminded: false,
      created_at: nowIso(),
    };
    scheduleFixture.push(item);
    return clone(item);
  },

  async updateSchedule(id: string, patch: SchedulePatch): Promise<ScheduleItem> {
    await delay();
    const item = scheduleFixture.find((s) => s.id === id);
    if (!item) throw new Error('schedule_not_found');
    Object.assign(item, patch);
    if (patch.location === undefined) item.location = item.location ?? null;
    return clone(item);
  },

  async deleteSchedule(id: string): Promise<DeleteResult> {
    await delay();
    const index = scheduleFixture.findIndex((s) => s.id === id);
    if (index >= 0) scheduleFixture.splice(index, 1);
    return { deleted: true };
  },

  async getTodos(query: TodoQuery = {}): Promise<Paged<Todo>> {
    await delay();
    const status = query.status ?? 'all';
    let items = [...todoFixture];
    if (status !== 'all') items = items.filter((t) => t.status === status);
    items.sort((a, b) => {
      if (a.status !== b.status) return a.status === 'pending' ? -1 : 1;
      return (a.due_at ?? '').localeCompare(b.due_at ?? '');
    });
    return paginate(items, query.limit ?? 50);
  },

  async createTodo(payload: TodoCreate): Promise<Todo> {
    await delay();
    const todo: Todo = {
      id: `todo_demo_${Date.now()}`,
      title: payload.title,
      status: 'pending',
      due_at: payload.due_at ?? null,
      source: 'manual',
      card_id: null,
      priority: payload.priority ?? 'normal',
      created_at: nowIso(),
      completed_at: null,
    };
    todoFixture.unshift(todo);
    return clone(todo);
  },

  async updateTodo(id: string, patch: TodoPatch): Promise<Todo> {
    await delay();
    const todo = todoFixture.find((t) => t.id === id);
    if (!todo) throw new Error('todo_not_found');
    Object.assign(todo, patch);
    if (patch.status === 'done') todo.completed_at = nowIso();
    if (patch.status === 'pending') todo.completed_at = null;
    return clone(todo);
  },

  async deleteTodo(id: string): Promise<DeleteResult> {
    await delay();
    const index = todoFixture.findIndex((t) => t.id === id);
    if (index >= 0) todoFixture.splice(index, 1);
    return { deleted: true };
  },

  async getMeetings(query: MeetingQuery = {}): Promise<Paged<Meeting>> {
    await delay();
    const items = meetingsFixture
      .filter((m) => {
        if (query.from && m.started_at < query.from) return false;
        if (query.to && m.started_at > query.to) return false;
        return true;
      })
      .sort((a, b) => b.started_at.localeCompare(a.started_at));
    return paginate(items, query.limit ?? 20);
  },

  async getMeeting(id: string): Promise<MeetingDetail> {
    await delay();
    const meeting = meetingsFixture.find((m) => m.id === id);
    if (!meeting) throw new Error('meeting_not_found');
    return clone(meeting);
  },

  async regenerateMeeting(id: string): Promise<MeetingDetail> {
    await delay(500);
    const meeting = meetingsFixture.find((m) => m.id === id);
    if (!meeting) throw new Error('meeting_not_found');
    meeting.minutes = `${meeting.minutes ?? '# 会议纪要\n'}\n\n---\n\n_（演示数据：纪要已于 ${new Date().toLocaleString('zh-CN')} 重新生成）_`;
    meeting.status = 'ready';
    return clone(meeting);
  },

  async patchMeeting(id: string, patch: MeetingPatch): Promise<MeetingDetail> {
    await delay();
    const meeting = meetingsFixture.find((m) => m.id === id);
    if (!meeting) throw new Error('meeting_not_found');
    if (patch.title !== undefined) meeting.title = patch.title;
    if (patch.summary !== undefined) meeting.summary = patch.summary;
    if (patch.minutes !== undefined) meeting.minutes = patch.minutes;
    if (patch.action_items !== undefined) meeting.action_items = patch.action_items;
    return clone(meeting);
  },

  async deleteMeeting(id: string): Promise<DeleteResult> {
    await delay();
    const index = meetingsFixture.findIndex((m) => m.id === id);
    if (index >= 0) meetingsFixture.splice(index, 1);
    return { deleted: true };
  },

  async getCommands(query: CommandQuery = {}): Promise<Paged<Command>> {
    await delay();
    const items = commandsFixture
      .filter((c) => !query.status || c.status === query.status)
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
    return paginate(items, query.limit ?? 20);
  },

  async retryCommand(id: string): Promise<Command> {
    await delay(300);
    const command = commandsFixture.find((c) => c.id === id);
    if (!command) throw new Error('command_not_found');
    command.status = 'done';
    command.pushed = true;
    command.result = `${command.result ?? ''}\n（演示数据：已重新执行并推送成功）`.trim();
    return clone(command);
  },

  async getAudit(query: AuditQuery = {}): Promise<Paged<AuditEntry>> {
    await delay();
    const items = [...auditFixture].sort((a, b) => (b.at ?? '').localeCompare(a.at ?? ''));
    return paginate(items, query.limit ?? 20);
  },

  async getDevices(): Promise<{ items: Device[] }> {
    await delay();
    return { items: clone(devicesFixture) };
  },

  async putDeviceConfig(id: string, config: DeviceConfig): Promise<DeviceConfigSaved> {
    await delay();
    let device = devicesFixture.find((d) => d.id === id);
    if (!device) {
      device = {
        id,
        name: id,
        last_seen: nowIso(),
        config,
        status: 'online',
      };
      devicesFixture.push(device);
    } else {
      device.config = { ...device.config, ...config };
      device.last_seen = nowIso();
    }
    return { saved: true, updated_at: nowIso() };
  },

  async pushTest(text: string): Promise<PushTestResponse> {
    await delay();
    auditFixture.unshift({
      id: `audit_demo_${Date.now()}`,
      at: nowIso(),
      action: 'push.test',
      entity: 'push',
      entity_id: 'dingtalk',
      before: null,
      after: { text, sent: true },
      note: '钉钉推送自检（演示）',
    });
    return { sent: true, provider: 'dingtalk' };
  },

  async search(query: string, provider: SearchProvider = 'builtin'): Promise<SearchResponse> {
    await delay(240);
    return {
      query,
      provider,
      answer: `（演示数据）关于「${query}」的归纳结果：这里会展示 AI 归纳后的答案。`,
      results: [
        {
          title: '示例结果：OpenAssistant 使用说明',
          url: 'https://example.com/openassistant',
          snippet: '这是演示模式返回的示例搜索结果，不会发起真实网络请求。',
        },
      ],
      took_ms: 128,
    };
  },
};

/** 供测试与页面复用的 fixtures 只读快照。 */
export const demoFixtures = {
  cards: cardsFixture,
  todos: todoFixture,
  schedule: scheduleFixture,
  meetings: meetingsFixture,
  commands: commandsFixture,
  audit: auditFixture,
  me: meFixture,
  get profile() {
    return profileFixture;
  },
};

function formatClock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}
