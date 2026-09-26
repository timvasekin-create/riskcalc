# -*- coding: utf-8 -*-
"""Проверка синтаксиса inline-JS в app/index.html и templates/index.html (node --check).

Запуск: python js_check.py → результат в js_check_out.txt
"""
import re
import os
import shutil
import subprocess

files = ["app/index.html", "templates/index.html"]
node = shutil.which("node")
out = open("js_check_out.txt", "w", encoding="utf-8")
out.write(f"node: {node}\n")
total = ok = 0
for f in files:
    html = open(f, encoding="utf-8", errors="replace").read()
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    for i, b in enumerate(blocks):
        total += 1
        if not node:
            continue
        p = f"__js_tmp_{i}.js"
        open(p, "w", encoding="utf-8").write(b)
        r = subprocess.run([node, "--check", p], capture_output=True, text=True)
        if r.returncode == 0:
            ok += 1
        else:
            out.write(f"FAIL {f} block#{i}: {r.stderr.strip()[:400]}\n")
        os.remove(p)
# Дубли id ломают getElementById (тихо) — проверяем отдельно, без node
for f in files:
    html = open(f, encoding="utf-8", errors="replace").read()
    ids = re.findall(r'\bid="([^"]+)"', html)
    dups = sorted({i for i in ids if ids.count(i) > 1})
    out.write(f"ids in {f}: {len(ids)}, dupes: {', '.join(dups) if dups else 'none'}\n")
out.write(f"blocks: {total}, ok: {ok}\n")
out.write("ALL JS OK" if node and ok == total else ("NO NODE" if not node else "SOME FAILED"))
out.close()
print("done")
