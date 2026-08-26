/**
 * "edited 3 minutes ago".
 *
 * Small, and worth a test for one reason: it takes a string from the server and
 * a clock, and both of those have a way of disagreeing. A project saved on a
 * machine whose clock is a few seconds fast produces a *negative* age, and
 * "edited -2 seconds ago" is the kind of thing that ships.
 */

import { describe, expect, it } from 'vitest'

import { relativeTime } from '@/app/projects/projects-client'

const NOW = Date.parse('2026-08-25T12:00:00Z')
const ago = (ms: number) => new Date(NOW - ms).toISOString()

const SECOND = 1000
const MINUTE = 60 * SECOND
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

describe('relativeTime', () => {
  it('says "just now" under a minute', () => {
    expect(relativeTime(ago(5 * SECOND), NOW)).toBe('just now')
  })

  it('counts minutes, then hours, then days', () => {
    expect(relativeTime(ago(3 * MINUTE), NOW)).toBe('3 minutes ago')
    expect(relativeTime(ago(5 * HOUR), NOW)).toBe('5 hours ago')
    expect(relativeTime(ago(3 * DAY), NOW)).toBe('3 days ago')
  })

  it('gets the singular right', () => {
    expect(relativeTime(ago(MINUTE), NOW)).toBe('1 minute ago')
    expect(relativeTime(ago(HOUR), NOW)).toBe('1 hour ago')
    expect(relativeTime(ago(DAY), NOW)).toBe('1 day ago')
  })

  it('falls back to a date after a week', () => {
    // Nobody reads "edited 34 days ago" as a time.
    expect(relativeTime(ago(30 * DAY), NOW)).toMatch(/\d/)
    expect(relativeTime(ago(30 * DAY), NOW)).not.toContain('ago')
  })

  it('does not produce a negative age when the server clock is ahead', () => {
    expect(relativeTime(new Date(NOW + 4 * SECOND).toISOString(), NOW)).toBe('just now')
  })

  it('says something rather than NaN when the timestamp is unreadable', () => {
    expect(relativeTime('not a date', NOW)).toBe('recently')
  })
})
