import { CompletionContext, snippetCompletion, type Completion } from '@codemirror/autocomplete'
import { syntaxTree } from '@codemirror/language'

const keywords: Record<string, string> = {
  cpp: 'auto bool break case catch char class const constexpr continue default delete double else enum explicit false float for if inline int long namespace new nullptr private protected public return short signed sizeof static struct switch template this throw true try typedef typename union unsigned using virtual void volatile while',
  python3:
    'and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield',
  java17:
    'abstract boolean break byte case catch char class const continue default do double else enum extends final finally float for if implements import instanceof int interface long native new null package private protected public return short static super switch synchronized this throw throws transient try void volatile while true false',
}
const library: Record<string, string> = {
  cpp: 'std std::vector std::string std::array std::pair std::map std::set std::unordered_map std::unordered_set std::queue std::stack std::priority_queue std::sort std::lower_bound std::upper_bound std::min std::max std::cin std::cout std::endl vector string pair map set sort lower_bound upper_bound push_back size begin end',
  python3:
    'print input int float str list dict set tuple range enumerate zip sorted reversed len min max sum abs any all append split strip join collections heapq bisect math sys',
  java17:
    'System String StringBuilder Scanner Arrays Collections ArrayList HashMap HashSet PriorityQueue Math BufferedReader InputStreamReader PrintWriter println length size add get contains sort',
}
export const languageKey = (language: string) => (language.startsWith('cpp') ? 'cpp' : language)
export function languageCompletions(language: string): Completion[] {
  const key = languageKey(language)
  const result: Completion[] = [
    ...(keywords[key] || '')
      .split(' ')
      .filter(Boolean)
      .map((label) => ({ label, type: 'keyword', detail: '语言关键字' })),
    ...(library[key] || '')
      .split(' ')
      .filter(Boolean)
      .map((label) => ({ label, type: 'variable', detail: '常用名称 · 非类型推断' })),
  ]
  const snippets =
    key === 'cpp'
      ? [
          ['for-loop', 'for (int ${i} = 0; ${i} < ${n}; ++${i}) {\n    ${}\n}'],
          ['if-block', 'if (${condition}) {\n    ${}\n}'],
          ['main', 'int main() {\n    ${}\n    return 0;\n}'],
        ]
      : key === 'python3'
        ? [
            ['for-loop', 'for ${item} in ${items}:\n    ${}'],
            ['if-block', 'if ${condition}:\n    ${}'],
            ['def-function', 'def ${name}(${args}):\n    ${}'],
          ]
        : [
            ['for-loop', 'for (int ${i} = 0; ${i} < ${n}; ++${i}) {\n    ${}\n}'],
            ['if-block', 'if (${condition}) {\n    ${}\n}'],
            ['main', 'public static void main(String[] args) {\n    ${}\n}'],
          ]
  return [
    ...result,
    ...snippets.map(([label, template]) =>
      snippetCompletion(template, { label, detail: '基础语法片段', type: 'snippet' }),
    ),
  ]
}

export function localCompletion(language: string, enabled: () => boolean = () => true) {
  const builtins = languageCompletions(language)
  return (context: CompletionContext) => {
    if (!enabled()) return null
    const node = syntaxTree(context.state).resolveInner(context.pos, -1)
    if (/Comment|String|Char/.test(node.name)) return null
    const word = context.matchBefore(/[\w:]+/)
    if (!word && !context.explicit) return null
    const known = new Set(builtins.map((c) => c.label))
    const words = new Set(context.state.doc.toString().match(/\b[A-Za-z_]\w*\b/g) || [])
    const local: Completion[] = []
    for (const name of words) {
      if (!known.has(name) && name !== word?.text && local.length < 500)
        local.push({ label: name, type: 'text', detail: '当前文件单词' })
    }
    return {
      from: word?.from ?? context.pos,
      options: [...builtins, ...local],
      validFor: /^[\w:]*$/,
    }
  }
}
