"""Development checks for CF's canonical Markdown, not live crawl acceptance."""
from acmweb.statement_markdown import RENDER_VERSION, html_markdown, upgrade_statement
from acmweb.translations import Document, fingerprint
from acmweb.workbench import parse_statement


RICH = '''<div class="problem-statement"><div class="header"><div class="title">A. Formatting</div></div>
<div><p>Given <span>$$$a_i \\le 10^9$$$</span>, use <strong>two</strong> items.</p>
<p>Literal a_b and *stars*.</p><img src="/diagram.png" alt="Diagram"><ul><li>First</li><li>Second</li></ul>
<table><tr><th>Name</th><th>Value</th></tr><tr><td>x</td><td>2</td></tr></table>
<pre><code>if (x &lt; y) {\n\n\n    a_b(); // ```\n}</code></pre></div>
<div class="input-specification"><div class="section-title">Input</div><p>Read n.<br>Then m.</p></div>
<div class="sample-tests"><div class="sample-test"><div class="input"><pre>2<br>3</pre></div><div class="output"><pre>5</pre></div></div></div></div>'''


def test_cf_response_has_canonical_markdown_and_separate_samples():
    result = parse_statement(RICH, 'codeforces:4-A')
    md = result['markdown']
    assert result['render_version'] == RENDER_VERSION and result['sections'] == [md]
    assert '$a_i \\le 10^9$' in md and '$$$' not in md
    assert '**two**' in md and '- First' in md and '## Input' in md
    assert '![Diagram](<https://codeforces.com/diagram.png>)' in md
    assert '| Name | Value |\n| --- | --- |' in md
    assert '````\nif (x < y) {\n\n\n    a_b(); // ```\n}\n````' in md
    assert 'a\\_b and \\*stars\\*' in md and 'Read n.  \nThen m.' in md
    assert '<p>' not in md and '<strong>' not in md
    assert result['samples'] == [{'input': '2\n3', 'output': '5'}]


def test_converter_preserves_code_line_breaks_and_rejects_unsafe_links():
    md = html_markdown('<pre>first<br>second</pre><script>alert(1)</script><img src="javascript:evil()"><a href="https://safe.test/a">link</a><a href="javascript:evil()">text</a>')
    assert 'first\nsecond' in md
    assert '[link](<https://safe.test/a>)' in md and md.endswith('text')
    assert 'alert' not in md and 'javascript:' not in md
    assert html_markdown('<p>Read\n    one\n    number.</p>') == 'Read one number.'


def test_cf_teletype_and_ascii_not_equal_keep_the_original_operator():
    md = html_markdown(
        '<p><span class="tex-font-style-tt">a != b</span> and $$$a != b$$$.</p>'
    )
    assert '`a != b`' in md
    assert '$a \\texttt{!=} b$' in md


def test_literal_math_placeholder_text_does_not_collide_with_generated_tokens():
    literal = 'ACMMATHPLACEHOLDER0END ACMMATHPLACEHOLDERX9END'
    assert html_markdown(f'<p>{literal}</p>') == literal
    assert html_markdown(f'<p>{literal} and $$$x_i$$$.</p>') == literal + ' and $x_i$.'


def test_old_rich_cf_cache_upgrades_without_network_and_preserves_translation():
    old = {'id': 'codeforces:4-A', 'body_html': '<p><b>Read</b> $$$x_i$$$.</p>', 'sections': ['Read\nx_i'], 'render_version': 2}
    upgraded = upgrade_statement(old)
    assert old['render_version'] == 2
    assert upgraded['markdown'] == '**Read** $x_i$.' and upgraded['render_version'] == RENDER_VERSION
    assert fingerprint(old) == fingerprint(upgraded)
    changed_prose = {**upgraded, 'body_html': '<p><b>Print</b> $$$x_i$$$.</p>'}
    changed_formula = {**upgraded, 'body_html': '<p><b>Read</b> $$$x_i + 1$$$.</p>'}
    assert fingerprint(upgraded) != fingerprint(changed_prose)
    assert fingerprint(upgraded) != fingerprint(changed_formula)
    assert upgrade_statement(upgraded) is upgraded
    plain = {'id': 'codeforces:4-A', 'sections': ['old plain text']}
    assert upgrade_statement(plain) is plain


def test_translated_cf_markdown_is_regenerated_from_translated_html():
    source = parse_statement(RICH, 'codeforces:4-A')
    doc = Document(source)
    translated = doc.apply({'blocks': [{**block, 'text': block['text'].replace('Given', '给定').replace('Read n.', '读取 n。')} for block in doc.blocks]})
    assert '给定' in translated['markdown'] and '读取 n。' in translated['markdown']
    assert 'Given' not in translated['markdown'] and translated['sections'] == [translated['markdown']]
    assert source['samples'] == translated['samples'] and source['images'] == translated['images']
    assert '$a_i \\le 10^9$' in translated['markdown']


def test_cf_crawl_translation_and_cache_upgrade_preserve_original_not_equal_tex():
    rendered = r'''<span class="katex"><span class="katex-mathml"><math><semantics>
    <mrow><mi>a</mi><mo>≠</mo><mi>b</mi></mrow>
    <annotation encoding="application/x-tex">a \neq b</annotation>
    </semantics></math></span><span class="katex-html">VISUAL DUPLICATE</span></span>'''
    html = '<div class="problem-statement"><div class="header"><div class="title">A. Relations</div></div><p>Given ' + rendered + '.</p><p><span class="tex-font-style-tt">a != b</span></p></div>'
    statement = parse_statement(html, 'codeforces:4-A')
    assert statement['markdown'].startswith(r'Given $a \neq b$.')
    assert statement['markdown'].endswith('`a != b`')
    assert statement['markdown'].count(r'\neq') == 1
    assert 'VISUAL DUPLICATE' not in statement['body_html']
    assert '<annotation' not in statement['body_html']
    doc = Document(statement)
    assert not any(r'\neq' in block['text'] or '!=' in block['text'] for block in doc.blocks)
    translated = doc.apply({'blocks': [
        {**block, 'text': block['text'].replace('Given', '给定')} for block in doc.blocks]})
    assert r'给定 $a \neq b$.' in translated['markdown']
    assert '`a != b`' in translated['markdown']
    cached = {**statement, 'render_version': 4, 'markdown': 'OLD BROKEN OUTPUT'}
    assert r'$a \neq b$' in upgrade_statement(cached)['markdown']


def test_mathml_split_negation_and_mathjax_tex_survive_html_sanitization():
    mathml = '<math><mrow><mi>a</mi><mo>=</mo><mo>&#x0338;</mo><mi>b</mi></mrow></math>'
    enclosed = '<math><menclose notation="updiagonalstrike"><mo>=</mo></menclose></math>'
    body = (f'<p>{mathml} and {enclosed}</p>'
            r'<p><script type="math/tex; mode=display">c \not= d</script></p>'
            '<script>alert("never execute")</script>')
    html = '<div class="problem-statement"><div class="header"><div class="title">A. Relations</div></div>' + body + '</div>'
    statement = parse_statement(html, 'codeforces:4-A')
    assert '$a≠b$' in statement['markdown']
    assert r'$\neq $' in statement['markdown']
    assert '$$\nc \\not= d\n$$' in statement['markdown']
    assert 'alert' not in statement['markdown'] and '<script' not in statement['body_html']
