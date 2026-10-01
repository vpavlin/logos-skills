#!/usr/bin/env python3
"""Make every Text / LogosText / Label in a QML file render PLAIN text.

Qt's default textFormat (AutoText) renders anything that looks like HTML, and a remote <img> in it
is fetched as soon as the item is shown, so peer-supplied text (titles, names, questions) can make a
reader's machine contact a stranger's server (IP leak) or fake UI with <b>/<br>. Inserts
`textFormat: Text.PlainText;` right after the opening brace of each such object that doesn't set a
textFormat already. TextField/TextArea/TextEdit/TextInput are untouched (they don't render HTML).

  qml-plaintext.py FILE...          rewrite in place, print a count per file
  qml-plaintext.py --check FILE...  exit 1 if any object would still be changed
"""
import re, sys

OPEN = re.compile(r'(?<![\w.])(LogosText|Text|Label)\s*\{')

def object_end(src, brace):
    """Index of the '}' closing the '{' at `brace` (skips strings and // comments)."""
    depth, i, n = 0, brace, len(src)
    while i < n:
        c = src[i]
        if c in '"\'':
            q = c; i += 1
            while i < n and src[i] != q:
                i += 2 if src[i] == '\\' else 1
        elif src.startswith('//', i):
            i = src.find('\n', i)
            if i < 0: return n
        elif c == '{': depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0: return i
        i += 1
    return n

def own_body(src, brace, end):
    """The object's body without nested objects' bodies (so a child's textFormat doesn't count)."""
    out, i, depth = [], brace + 1, 0
    while i < end:
        c = src[i]
        if c == '{': depth += 1
        elif c == '}': depth -= 1
        elif depth == 0: out.append(c)
        i += 1
    return ''.join(out)

def transform(src):
    edits = []
    for m in OPEN.finditer(src):
        # skip matches inside // comments or strings on this line (cheap heuristic: comment marker before it)
        line_start = src.rfind('\n', 0, m.start()) + 1
        if '//' in src[line_start:m.start()]: continue
        brace = m.end() - 1
        end = object_end(src, brace)
        if 'textFormat' in own_body(src, brace, end): continue
        edits.append(brace + 1)
    for pos in reversed(edits):
        src = src[:pos] + ' textFormat: Text.PlainText;' + src[pos:]
    return src, len(edits)

if __name__ == '__main__':
    check = '--check' in sys.argv
    bad = 0
    for f in [a for a in sys.argv[1:] if a != '--check']:
        src = open(f, encoding='utf-8').read()
        new, n = transform(src)
        print(f'{f}: {n} element(s) {"to change" if check else "changed"}')
        if check: bad += n
        elif n: open(f, 'w', encoding='utf-8').write(new)
    sys.exit(1 if bad else 0)
