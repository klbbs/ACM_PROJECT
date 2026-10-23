"""Convert sanitized statement HTML to Markdown before it reaches readers or AI.

TeX is protected from Markdown escaping; raw HTML is never needed by the renderer.
"""
import re
from urllib.parse import quote, urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString

RENDER_VERSION = 3
MATH = re.compile(r'\$\$\$([\s\S]*?)\$\$\$|\\\(([\s\S]*?)\\\)|\\\[([\s\S]*?)\\\]|\$\$([\s\S]*?)\$\$|(?<!\\)\$([^$\n]+?)(?<!\\)\$')


def _url(raw):
    try:
        parsed = urlsplit(raw or '')
        if re.search(r'[\x00-\x20\\]', raw or ''):
            return ''
        return quote(raw, safe=':/?#[]@!$&\'()*+,;=%') if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password else ''
    except ValueError:
        return ''


def html_markdown(html):
    soup = BeautifulSoup(html, 'html.parser')

    def text(value):
        formulas = []
        prefix = 'ACMMATHPLACEHOLDER'
        # A literal token in a problem statement must stay literal, including
        # when that text node contains no formula at all.
        while prefix in value:
            prefix += 'X'

        def protect(match):
            inline = match[1] if match[1] is not None else match[2] if match[2] is not None else match[5]
            tex = inline if inline is not None else match[3] if match[3] is not None else match[4]
            formulas.append(f'${tex}$' if inline is not None else f'\n\n$$\n{tex}\n$$\n\n')
            return f'{prefix}{len(formulas) - 1}END'

        value = MATH.sub(protect, value.replace('\xa0', ' '))
        # HTML source indentation is not a Markdown code block. Explicit <br>
        # and <pre> are handled separately; ordinary HTML whitespace collapses.
        value = re.sub(r'[ \t\r\n\f]+', ' ', value)
        value = re.sub(r'([\\`*_[\]<>])', r'\\\1', value)
        # Keep literal prose from accidentally becoming a heading/list.
        value = re.sub(r'(?m)^(\s*)([#>+-]|\d+[.)])(?=\s)', r'\1\\\2', value)
        return re.sub(re.escape(prefix) + r'(\d+)END', lambda m: formulas[int(m[1])], value)

    def render(node):
        if isinstance(node, Comment):
            return ''
        if isinstance(node, NavigableString):
            return text(str(node))
        name = node.name
        if name in {'script', 'style', 'iframe', 'object', 'svg', 'form', 'button'}:
            return ''
        if name == 'pre':
            copy = BeautifulSoup(str(node), 'html.parser')
            for br in copy.select('br'):
                br.replace_with('\n')
            for line in copy.select('.test-example-line'):
                line.append('\n')
            value = copy.get_text('', strip=False).strip('\n')
            fence = '`' * max(3, max((len(x) + 1 for x in re.findall(r'`+', value)), default=3))
            return f'\n\n{fence}\n{value}\n{fence}\n\n'
        if name == 'code':
            value = node.get_text('', strip=False)
            fence = '`' * max(1, max((len(x) + 1 for x in re.findall(r'`+', value)), default=1))
            return f'{fence} {value} {fence}' if '`' in value else f'{fence}{value}{fence}'
        if name == 'img':
            url = _url(node.get('src'))
            alt = re.sub(r'[\[\]\\\n]', '', node.get('alt') or '题目配图')
            return f'\n\n![{alt}](<{url}>)\n\n' if url else ''
        if name == 'br':
            return '  \n'
        if name == 'table':
            rows = []
            for row in node.find_all('tr'):
                cells = [''.join(render(c) for c in cell.children).strip().replace('|', r'\|').replace('\n', ' ') for cell in row.find_all(['td', 'th'], recursive=False)]
                if cells:
                    rows.append(cells)
            if not rows:
                return ''
            width = max(map(len, rows))
            rows = [r + [''] * (width - len(r)) for r in rows]
            rows.insert(1, ['---'] * width)
            return '\n\n' + '\n'.join('| ' + ' | '.join(row) + ' |' for row in rows) + '\n\n'
        value = ''.join(render(c) for c in node.children)
        if name == 'a':
            url = _url(node.get('href'))
            return f'[{value}](<{url}>)' if url else value
        if name in {'strong', 'b', 'em', 'i'}:
            delimiter = '**' if name in {'strong', 'b'} else '*'
            return delimiter + value.strip() + delimiter if value.strip() else value
        if name in {'sup', 'sub'}:
            return '$' + ('^' if name == 'sup' else '_') + '{' + node.get_text() + '}$'
        if name and re.fullmatch(r'h[1-6]', name):
            return '\n\n' + '#' * int(name[1]) + ' ' + value.strip() + '\n\n'
        if name in {'ul', 'ol'}:
            rows = []
            for i, item in enumerate(node.find_all('li', recursive=False), 1):
                content = ''.join(render(c) for c in item.children).strip()
                prefix = f'{i}. ' if name == 'ol' else '- '
                rows.append(prefix + content.replace('\n', '\n' + ' ' * len(prefix)))
            return '\n\n' + '\n'.join(rows) + '\n\n'
        if name == 'blockquote':
            return '\n\n' + '\n'.join('> ' + line for line in value.strip().splitlines()) + '\n\n'
        if name == 'hr':
            return '\n\n---\n\n'
        if name in {'p', 'div', 'section'}:
            return '\n\n' + value.strip() + '\n\n'
        return value

    # Never normalize whitespace globally: doing so corrupts fenced code.
    return ''.join(render(child) for child in soup.children).strip()


def upgrade_statement(statement):
    """Upgrade rich CF caches locally; missing HTML must be fetched again."""
    if not statement or not statement.get('id', '').startswith('codeforces:') or not statement.get('body_html'):
        return statement
    if statement.get('render_version') == RENDER_VERSION and isinstance(statement.get('markdown'), str):
        return statement
    result = dict(statement)
    result['markdown'] = html_markdown(result['body_html'])
    result['sections'] = [result['markdown']]
    result['render_version'] = RENDER_VERSION
    return result
