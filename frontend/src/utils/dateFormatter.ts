/**
 * 日期格式化工具函数
 *
 * 统一管理日期转换逻辑，避免各组件重复实现（DRY 原则）
 */

/** 将 ISO 时间戳转换为 <input type="datetime-local"> 所需格式 */
export function toDatetimeLocal(value?: string | null): string {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    const offset = date.getTimezoneOffset() * 60000;
    return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

/** 将 datetime-local 输入值转换为 ISO 字符串 */
export function toIso(value: string): string | undefined {
    if (!value) return undefined;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

/** 将 ISO 时间戳格式化为中文本地时间（2025-02-25 14:30） */
export function formatDateTimeCN(iso?: string | null): string {
    if (!iso) return '-';
    const d = new Date(iso);
    return Number.isNaN(d.getTime())
        ? '-'
        : d.toLocaleString('zh-CN', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
        });
}
