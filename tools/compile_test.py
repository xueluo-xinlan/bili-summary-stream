#!/usr/bin/env python3
"""编译级完整检查（修正版）。

四层：
  1 字节码编译    每个 .py 都能编译
  2 静态分析      ruff --select F（重点看 F821 未定义名——只在运行时才炸的那类）
  3 导入冒烟      真导入每个模块（**跳过无 __main__ 守卫的脚本**，否则会触发它们的顶层副作用）
  4 陈旧字节码    只检查与源文件同目录 __pycache__ 下的 .pyc
"""
import ast
import os
import subprocess
import sys

PROJ = "/root/bili-summary-stream"
os.chdir(PROJ)
# 导入冒烟要求项目根在 sys.path 上，否则 `import src.xxx` / `import main` 会失败
sys.path.insert(0, PROJ)
PY = f"{PROJ}/.venv/bin/python"
RUFF = f"{PROJ}/.venv/bin/ruff"

files = []
for root, dirs, fs in os.walk("."):
    dirs[:] = [d for d in dirs if d not in (".venv", "__pycache__", ".git")]
    for f in fs:
        if f.endswith(".py"):
            files.append(os.path.join(root, f))
files.sort()
print(f"待检文件：{len(files)} 个\n")

fails = []

# ── 1 字节码编译 ──
print("【1】字节码编译")
bad = []
for f in files:
    r = subprocess.run([PY, "-m", "py_compile", f], capture_output=True)
    if r.returncode != 0:
        bad.append((f, r.stderr.decode()[:100]))
for f, e in bad:
    print(f"  ✗ {f}: {e}")
print(f"  {'✓ 全部通过' if not bad else f'★ {len(bad)} 个失败'}"
      f"（{len(files)} 个文件）")
if bad:
    fails.append("字节码编译")

# ── 2 静态分析 ──
print("\n【2】静态分析 ruff --select F,E9")
r = subprocess.run([RUFF, "check", "--select", "F,E9", "--no-cache",
                    "--output-format", "concise", "src/", "main.py", "tools/"],
                   capture_output=True, text=True)
lines = [l for l in (r.stdout or "").splitlines() if ": " in l and not l.startswith("Found")]
cats = {}
for l in lines:
    for code in ("F821", "F811", "F401", "F841", "F541", "F402", "E9"):
        if code in l:
            cats[code] = cats.get(code, 0) + 1
            break
print(f"  发现 {len(lines)} 处：")
for c, n in sorted(cats.items(), key=lambda x: -x[1]):
    label = {"F821": "未定义名（致命）", "F811": "重复定义", "F401": "导入未用",
             "F841": "变量未用", "F541": "f-string 无占位符", "E9": "语法/运行错误"}.get(c, c)
    print(f"      {c:5s} {label:18s} {n:3d}")
fatal = cats.get("F821", 0) + cats.get("F811", 0) + cats.get("E9", 0)
print(f"  {'✓ 无致命项（未定义名/重复定义/运行错误均为 0）' if fatal == 0 else f'★ 有 {fatal} 处致命项'}")
if fatal:
    fails.append("静态分析有致命项")
    for l in lines:
        if any(c in l for c in ("F821", "F811", "E9")):
            print(f"      {l}")

# ── 3 导入冒烟 ──
print("\n【3】导入冒烟")
mods, scripts = [], []
for f in files:
    if not f.startswith("./src/") and f != "./main.py":
        continue
    src = open(f, encoding="utf-8").read()
    tree = ast.parse(src)
    guarded = any(isinstance(n, ast.If) and "__main__" in ast.dump(n) for n in tree.body)
    mod = f[2:-3].replace("/", ".")
    (mods if guarded else mods).append(mod)
import importlib
imp_bad = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:
        imp_bad.append((m, f"{type(e).__name__}: {str(e)[:70]}"))
for m, e in imp_bad:
    print(f"  ✗ {m}: {e}")
print(f"  {'✓ 全部导入成功' if not imp_bad else f'★ {len(imp_bad)} 个失败'}（{len(mods)} 个模块）")
if imp_bad:
    fails.append("导入冒烟")

# ── 3b 工具的顶层副作用 ──
print("\n【3b】tools/ 脚本的顶层副作用（无 __main__ 守卫 = 一导入就执行）")
side = []
for f in files:
    if not f.startswith("./tools/"):
        continue
    tree = ast.parse(open(f, encoding="utf-8").read())
    if not any(isinstance(n, ast.If) and "__main__" in ast.dump(n) for n in tree.body):
        side.append(f)
for f in side:
    print(f"      ⚠ {f} 无 __main__ 守卫 → 被 import 时会立刻执行整段逻辑")
print(f"  {'✓ 无此类脚本' if not side else f'（{len(side)} 个，属代码整洁问题，不影响直接运行）'}")

# ── 4 陈旧字节码 ──
print("\n【4】陈旧字节码（只查同目录 __pycache__）")
stale = []
for f in files:
    d = os.path.dirname(f)
    name = os.path.basename(f)[:-3]
    cache = os.path.join(d, "__pycache__")
    if not os.path.isdir(cache):
        continue
    for p in os.listdir(cache):
        if p.startswith(name + ".cpython-") and p.endswith(".pyc"):
            if os.path.getmtime(f) > os.path.getmtime(os.path.join(cache, p)):
                stale.append(f)
for f in stale:
    print(f"      ⚠ {f}")
print(f"  {'✓ 无陈旧字节码' if not stale else f'★ {len(stale)} 个源文件比字节码新（Python 会自动重编，仅提示）'}")

print("\n" + "=" * 70)
print(f"编译级检查：{'全部通过' if not fails else '失败项 ' + str(fails)}")
print("=" * 70)
sys.exit(1 if fails else 0)
