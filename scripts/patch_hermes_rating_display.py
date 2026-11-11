#!/usr/bin/env python3
"""Opt-in numeric rating display for the local QQ bridge; no messages or AI calls."""
import argparse
import ast
import json
from pathlib import Path

from patch_hermes_bridge import apply

ANCHOR = "    tail = ['约束：', *('- ' + s for s in explanation['constraints'])]"
INSERT = '''    # ACM independent rating display v1; never read the solution report.
    assessment = result.get('ai_difficulty_assessment') or {}
    rating = assessment.get('rating') if isinstance(assessment, dict) else None
    if type(rating) in (int, float) and 0 <= rating <= 4000:
        lines.insert(1, 'AI 推测评分：' + str(rating) + '（非官方评级）')
'''


def plan(directory):
    path = Path(directory) / 'practice_text.py'
    if path.is_symlink() or not path.is_file():
        raise ValueError('practice_text.py must be a regular file')
    original = path.read_bytes()
    source = original.decode()
    if INSERT in source:
        return {'practice_text.py': original}, {}
    if 'ACM independent rating display' in source or source.count(ANCHOR) != 1:
        raise ValueError('unknown bridge layout; unchanged')
    functions = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)
                 and node.name == 'problem_sections']
    if len(functions) != 1 or ANCHOR not in ast.get_source_segment(source, functions[0]):
        raise ValueError('unknown question projection; unchanged')
    updated = source.replace(ANCHOR, INSERT + ANCHOR)
    compile(updated, str(path), 'exec')
    return {'practice_text.py': original}, {'practice_text.py': updated.encode()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    originals, changes = plan(args.directory)
    print(json.dumps({'files': list(changes), 'applied': bool(changes and args.apply)}))
    if args.apply:
        backup = apply(args.directory, originals, changes)
        if backup:
            print('Backup: ' + str(backup))


if __name__ == '__main__':
    main()
