export type EditorSettings = {
  keymap: 'standard' | 'vim'
  font_size: number
  line_wrapping: boolean
}
export const defaultEditorSettings: EditorSettings = {
  keymap: 'standard',
  font_size: 14,
  line_wrapping: false,
}
export const CODE_LIMIT = 65536
export const codeBytes = (value: string) => new TextEncoder().encode(value).byteLength
export type CodeEditorProps = {
  documentId: string
  value: string
  language: string
  settings: EditorSettings
  readOnly: boolean
  onChange: (value: string) => void
  onSave: () => Promise<boolean>
  onError: (message: string) => void
}
