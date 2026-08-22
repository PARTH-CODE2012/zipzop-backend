import { describe, expect, it } from 'vitest'

import { actionFor } from './keyboard'

const key = (
  over: Partial<Pick<KeyboardEvent, 'key' | 'code' | 'metaKey' | 'ctrlKey' | 'shiftKey'>>,
) => ({ key: '', code: '', metaKey: false, ctrlKey: false, shiftKey: false, ...over })

const editing = { isTyping: false }
const typing = { isTyping: true }

describe('actionFor', () => {
  it('maps the editing keys', () => {
    expect(actionFor(key({ code: 'Space' }), editing)).toBe('play-pause')
    expect(actionFor(key({ key: 's' }), editing)).toBe('split')
    expect(actionFor(key({ key: 'Delete' }), editing)).toBe('delete')
    expect(actionFor(key({ key: 'ArrowLeft' }), editing)).toBe('nudge-left')
  })

  it('distinguishes undo from redo by shift', () => {
    expect(actionFor(key({ key: 'z', metaKey: true }), editing)).toBe('undo')
    expect(actionFor(key({ key: 'z', metaKey: true, shiftKey: true }), editing)).toBe('redo')
    expect(actionFor(key({ key: 'z', ctrlKey: true }), editing)).toBe('undo')
  })

  it('fires nothing while the user is typing', () => {
    // Renaming a project and pressing `s` must write an `s`.
    expect(actionFor(key({ key: 's' }), typing)).toBeNull()
    expect(actionFor(key({ code: 'Space' }), typing)).toBeNull()
    expect(actionFor(key({ key: 'Backspace' }), typing)).toBeNull()
  })

  it('lets escape through from anywhere', () => {
    expect(actionFor(key({ key: 'Escape' }), typing)).toBe('cancel')
  })
})
