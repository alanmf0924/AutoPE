/** SQLite drops UTC offsets from persisted datetimes; API timestamps without one are UTC. */
export function parseBackendTime(value: string): number {
  const timestamp = value.trim()
  if (!timestamp) return Number.NaN
  const hasZone = /(?:Z|[+-]\d{2}:\d{2})$/i.test(timestamp)
  return Date.parse(hasZone ? timestamp : `${timestamp}Z`)
}

const beijingFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})

export function formatBackendTime(value: string): string {
  const timestamp = parseBackendTime(value)
  return Number.isFinite(timestamp)
    ? `${beijingFormatter.format(new Date(timestamp))} 北京时间`
    : '时间未知'
}
