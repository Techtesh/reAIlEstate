export function rotatedImageSize(width: number, height: number, degrees: number): [number, number] {
  const rotation = ((degrees % 360) + 360) % 360
  if (rotation % 180 === 0) return [width, height]
  if (rotation % 90 === 0) return [height, width]
  const radians = rotation * Math.PI / 180
  const cosine = Math.abs(Math.cos(radians)), sine = Math.abs(Math.sin(radians))
  // Pillow expands the transformed pixel edges around the original image centre.
  const extent = (original: number, span: number) => Math.ceil((original + span) / 2) - Math.floor((original - span) / 2)
  return [extent(width, width * cosine + height * sine), extent(height, width * sine + height * cosine)]
}
