import {
  addDays,
  differenceInCalendarDays,
  endOfDay,
  format,
  isToday,
  isTomorrow,
  isYesterday,
  startOfDay,
} from 'date-fns';
import { zhCN } from 'date-fns/locale';

export function parseDate(value: string | Date): Date {
  return value instanceof Date ? value : new Date(value);
}

export function formatDateTime(value: string | Date): string {
  return format(parseDate(value), 'yyyy年M月d日 HH:mm', { locale: zhCN });
}

export function formatShortDateTime(value: string | Date): string {
  return format(parseDate(value), 'M月d日 HH:mm', { locale: zhCN });
}

export function formatDate(value: string | Date): string {
  return format(parseDate(value), 'yyyy年M月d日', { locale: zhCN });
}

export function formatTime(value: string | Date): string {
  return format(parseDate(value), 'HH:mm');
}

export function formatWeekday(value: string | Date): string {
  return format(parseDate(value), 'EEEE', { locale: zhCN });
}

export function dayLabel(value: string | Date): string {
  const date = parseDate(value);
  if (isToday(date)) return '今天';
  if (isTomorrow(date)) return '明天';
  if (isYesterday(date)) return '昨天';
  const diff = differenceInCalendarDays(date, new Date());
  if (diff > 1 && diff <= 7) return `${formatWeekday(date)}（${format(date, 'M月d日')}）`;
  return format(date, 'M月d日 EEEE', { locale: zhCN });
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '—';
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  if (minutes < 60) return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  return restMinutes ? `${hours} 小时 ${restMinutes} 分` : `${hours} 小时`;
}

export function formatSecondsClock(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

export function startOfTodayIso(): string {
  return startOfDay(new Date()).toISOString();
}

export function endOfTodayIso(): string {
  return endOfDay(new Date()).toISOString();
}

export function daysFromNowIso(days: number, end = false): string {
  const target = addDays(new Date(), days);
  return (end ? endOfDay(target) : startOfDay(target)).toISOString();
}

export function isPast(value: string | Date): boolean {
  return parseDate(value).getTime() < Date.now();
}

export function relativeLabel(value: string | Date): string {
  const date = parseDate(value);
  const diffMs = Date.now() - date.getTime();
  const minutes = Math.round(diffMs / 60_000);
  if (Math.abs(minutes) < 1) return '刚刚';
  if (minutes > 0 && minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 0 && minutes > -60) return `${Math.abs(minutes)} 分钟后`;
  const hours = Math.round(minutes / 60);
  if (hours > 0 && hours < 24) return `${hours} 小时前`;
  const days = differenceInCalendarDays(new Date(), date);
  if (days === 0) return '今天';
  if (days === 1) return '昨天';
  if (days > 1 && days < 30) return `${days} 天前`;
  return formatDate(date);
}

/** 把 ISO 时间转为 <input type="datetime-local"> 需要的本地格式。 */
export function toDateTimeLocal(value?: string | null): string {
  if (!value) return '';
  const date = parseDate(value);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`;
}

/** datetime-local 值 → 带本地时区偏移的 ISO 字符串（契约要求带时区）。 */
export function fromDateTimeLocal(value: string): string {
  return new Date(value).toISOString();
}

export function groupByDay<T>(items: T[], getDate: (item: T) => string): { day: string; items: T[] }[] {
  const groups: { day: string; items: T[] }[] = [];
  for (const item of items) {
    const iso = getDate(item);
    const key = startOfDay(parseDate(iso)).toISOString();
    const existing = groups.find((group) => group.day === key);
    if (existing) existing.items.push(item);
    else groups.push({ day: key, items: [item] });
  }
  return groups;
}
