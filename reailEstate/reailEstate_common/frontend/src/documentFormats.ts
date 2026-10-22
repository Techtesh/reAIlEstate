// Keep evidence formats aligned with platform_api/document_formats.py.
const documentFormats: Record<string, string> = {
  pdf: 'application/pdf', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg',
  txt: 'text/plain', md: 'text/markdown', markdown: 'text/markdown',
  csv: 'text/csv', json: 'application/json',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  doc: 'application/msword', rtf: 'application/rtf', odt: 'application/vnd.oasis.opendocument.text',
  ppt: 'application/vnd.ms-powerpoint', xls: 'application/vnd.ms-excel',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
}
export const documentAccept = Object.keys(documentFormats).map(extension => `.${extension}`).join(',')
export function documentMediaType(filename: string): string | undefined {
  // Browsers often leave Markdown/Office MIME types empty or report text/plain.
  // The backend independently verifies the uploaded bytes against this declaration.
  const extension = filename.slice(filename.lastIndexOf('.') + 1).toLowerCase()
  return filename.includes('.') && Object.hasOwn(documentFormats, extension) ? documentFormats[extension] : undefined
}
