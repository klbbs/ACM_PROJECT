export type CodeTemplate = {
  id: string
  title: string
  trigger: string
  language: 'all' | 'cpp' | 'cpp17' | 'cpp20' | 'python3' | 'java17'
  body: string
}
export type TemplateDocument = { revision: number; items: CodeTemplate[] }
export const templateLanguageNames: Record<CodeTemplate['language'], string> = {
  all: '全部语言',
  cpp: 'C++17 / C++20',
  cpp17: 'C++17',
  cpp20: 'C++20',
  python3: 'Python 3',
  java17: 'Java 17',
}
export const templateMatchesLanguage = (template: CodeTemplate, language: string) =>
  template.language === 'all' ||
  template.language === language ||
  (template.language === 'cpp' && language.startsWith('cpp'))
