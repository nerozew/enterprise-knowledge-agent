export function totalTimingMs(timings: Record<string, number>): number {
  return timings.total ?? Object.values(timings).reduce((sum, value) => sum + value, 0)
}
