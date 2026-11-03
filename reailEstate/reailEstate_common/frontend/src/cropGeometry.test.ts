import { describe, expect, it } from 'vitest'
import { rotatedImageSize } from './cropGeometry'

describe('crop preview expansion', () => {
  it.each([
    [200, 100, 5, 208, 118], [200, 100, 10, 216, 134],
    [200, 100, 355, 208, 118], [201, 100, 5, 209, 118],
    [201, 100, 90, 100, 201], [201, 100, 180, 201, 100],
    [201, 100, 270, 100, 201], [201, 100, 360, 201, 100],
  ])('matches saved image dimensions for %sx%s at %s degrees', (width, height, degrees, expectedWidth, expectedHeight) => {
    expect(rotatedImageSize(width, height, degrees)).toEqual([expectedWidth, expectedHeight])
  })
})
