import { describe, expect, it } from 'vitest'
import { documentAccept, documentMediaType } from './documentFormats'

describe('evidence formats', () => {
  it.each([
    ['evidence.PDF', 'application/pdf'], ['photo.png', 'image/png'], ['photo.jpeg', 'image/jpeg'],
    ['notes.md', 'text/markdown'], ['notes.markdown', 'text/markdown'], ['notes.txt', 'text/plain'],
    ['brochure.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
    ['brochure.doc', 'application/msword'], ['notes.rtf', 'application/rtf'],
    ['notes.odt', 'application/vnd.oasis.opendocument.text'], ['prices.csv', 'text/csv'],
    ['facts.json', 'application/json'], ['prices.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
    ['slides.pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation'],
  ])('declares %s independently of browser MIME detection', (filename, mediaType) => {
    expect(documentMediaType(filename)).toBe(mediaType)
    expect(documentAccept).toContain(`.${filename.split('.').at(-1)!.toLowerCase()}`)
  })
  it.each(['photo.webp', 'archive.zip', 'video.mp4', 'run.exe', 'pdf', 'notes.txt.exe', 'notes.constructor', 'notes.__proto__'])('rejects unsupported %s', filename => {
    expect(documentMediaType(filename)).toBeUndefined()
  })
})
