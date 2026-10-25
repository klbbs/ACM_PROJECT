"""Recover mathematical source before an HTML sanitizer removes presentation tags.

Only text is produced. No upstream HTML or script is trusted by the renderer.
"""
import re
import unicodedata

from bs4 import NavigableString, Tag


def normalize_overlay(value):
    """MathML may split '=' and U+0338 into adjacent text nodes."""
    value = re.sub(r'=\s*\u0338|\u0338\s*=', '≠', value)
    return unicodedata.normalize('NFC', value)


def _mathml(node):
    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag) or node.name in {'annotation', 'annotation-xml'}:
        return ''
    children = [child for child in node.children if isinstance(child, Tag)]
    parts = [_mathml(child) for child in children]
    if node.name == 'semantics':
        return parts[0] if parts else ''
    if node.name in {'mi', 'mn', 'mo', 'mtext', 'ms'}:
        return normalize_overlay(node.get_text('', strip=False))
    if node.name in {'msup', 'msub'} and len(parts) == 2:
        return '{' + parts[0] + '}' + ('^' if node.name == 'msup' else '_') + '{' + parts[1] + '}'
    if node.name == 'msubsup' and len(parts) == 3:
        return '{' + parts[0] + '}_{' + parts[1] + '}^{' + parts[2] + '}'
    if node.name == 'mfrac' and len(parts) == 2:
        return r'\frac{' + parts[0] + '}{' + parts[1] + '}'
    if node.name == 'msqrt':
        return r'\sqrt{' + ''.join(parts) + '}'
    if node.name == 'mroot' and len(parts) == 2:
        return r'\sqrt[' + parts[1] + ']{' + parts[0] + '}'
    if node.name == 'menclose' and 'updiagonalstrike' in node.get('notation', '').split():
        value = ''.join(parts).strip()
        if value == '=':
            return r'\neq '
    return ''.join(_mathml(child) for child in node.children)


def _tex(node):
    for annotation in node.find_all('annotation'):
        if annotation.get('encoding', '').lower() in {
            'application/x-tex', 'application/x-latex', 'text/x-tex', 'application/tex'}:
            return annotation.get_text('', strip=False)
    math = node if node.name == 'math' else node.find('math')
    return normalize_overlay(_mathml(math)) if math is not None else ''


def preserve_math_markup(soup):
    """Replace rendered KaTeX/MathJax/MathML with original inline/display TeX.

    Call before removing scripts/classes/unknown tags. Ordinary code and ASCII
    operators are deliberately untouched, and TeX annotations win over visual
    glyphs so the same formula isn't copied three times.
    """
    for script in list(soup.find_all('script')):
        kind = script.get('type', '').lower().replace(' ', '')
        if kind not in {'math/tex', 'math/tex;mode=display'} or script.find_parent(['pre', 'code']):
            continue
        value = script.get_text('', strip=False)
        script_id = script.get('id')
        if script_id:
            frame = soup.find(id=script_id + '-Frame')
            if frame is not None and 'MathJax' in frame.get('class', []):
                frame.decompose()
        previous = script.find_previous_sibling()
        if previous is not None and 'MathJax_Preview' in previous.get('class', []):
            previous.decompose()
        delimiter = '$$' if 'mode=display' in kind else '$'
        script.replace_with(NavigableString(delimiter + value + delimiter))

    targets = list(soup.select('.katex-display, .katex, .MathJax, .MathJax_Display, math'))
    target_ids = {id(node) for node in targets}
    for node in targets:
        if node.parent is None or node.find_parent(['pre', 'code']):
            continue
        if any(id(parent) in target_ids for parent in node.parents):
            continue
        value = _tex(node)
        if not value.strip():
            continue
        block = (node.get('display') == 'block' or
                 bool(set(node.get('class', [])) & {'katex-display', 'MathJax_Display'}))
        delimiter = '$$' if block else '$'
        node.replace_with(NavigableString(delimiter + value + delimiter))

    # CF's teletype class is otherwise stripped by the HTML allowlist, losing
    # the distinction between a programming != and mathematical not-equal.
    for node in soup.select('span.tex-font-style-tt'):
        node.name = 'code'
        node.attrs = {}
    return soup
