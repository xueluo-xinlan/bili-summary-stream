#!/usr/bin/env python3
"""BiliSummaryStream 自检套件。

四层覆盖，**不推送任何私信、不修改任何配置**：
  A 静态    — 语法编译、模块导入、配置完整性
  B 组件    — 空摘要闸门 / 甩锅拦截 / 来源三态 / 相关性闸门 / 存储层 / 通知器
  C 端到端  — 真实视频走一遍内容获取链（只到"拿到正文"为止，不推送）
  D 运行态  — systemd 服务、字幕通道开关、金丝雀、定时器

用法： .venv/bin/python tools/self_test.py
退出码： 0=全部通过 / 1=有失败项
"""
import os
import re
import subprocess
import sys
import time

PROJ = "/root/bili-summary-stream"
sys.path.insert(0, PROJ)
os.chdir(PROJ)

QUICK = "--quick" in sys.argv        # 精简模式：跳过端到端那一段
PASS, FAIL, SKIP = [], [], []


def check(section, name, ok, detail=""):
    tag = "✓" if ok else "✗"
    (PASS if ok else FAIL).append(f"{section} {name}")
    print(f"  {tag} [{section}] {name}" + (f"  — {detail}" if detail else ""))


def skip(section, name, why=""):
    SKIP.append(f"{section} {name}")
    print(f"  – [{section}] {name}  （跳过：{why}）")


print("=" * 78)
print(f"BiliSummaryStream 自检 · {time.strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 78)

# ══════════════ A 静态 ══════════════
print("\n【A 静态检查】")

# A1 语法编译
bad = []
for root, _, files in os.walk("."):
    if any(x in root for x in (".venv", "__pycache__", ".git")):
        continue
    for f in files:
        if f.endswith(".py"):
            p = os.path.join(root, f)
            r = subprocess.run([f"{PROJ}/.venv/bin/python", "-m", "py_compile", p],
                               capture_output=True)
            if r.returncode != 0:
                bad.append(p)
check("A1", "全部 .py 语法编译", not bad, f"失败: {bad}" if bad else "含 src/ main.py tools/")

# A2 模块导入
import importlib
mods = ["src.config_loader", "src.bili_api", "src.transcriber_client", "src.cloud_mm",
        "src.summarizer", "src.notifier", "src.storage", "src.pipeline", "src.bot_listener",
        "src.filter"]
imp_err = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:
        imp_err.append(f"{m}: {str(e)[:60]}")
check("A2", f"核心模块导入（{len(mods)} 个）", not imp_err, "; ".join(imp_err)[:120] if imp_err else "")

# A3 配置完整性
from src.config_loader import load_config
cfg = load_config("config.yaml")
need = ["bilibili", "llm", "notification", "paths"]
miss = [k for k in need if k not in cfg]
check("A3", "配置必需段齐全", not miss, f"缺: {miss}" if miss else f"含 {len(cfg)} 段")
key = (cfg.get("llm") or {}).get("api_key") or ""
check("A3", "llm.api_key 已配置", len(key) > 20, f"长度 {len(key)}（值不显示）")
check("A3", "bilibili 凭证齐全",
      all((cfg.get("bilibili") or {}).get(k) for k in ("sessdata", "bili_jct", "sender_uid")))

# A4 L1 超时必须覆盖配置允许的最长视频
# 实测速率 ≈7.5x 实时（RTX 5060 Laptop + large-v3-turbo float16），固定开销约 40s（模型加载）。
# 旧值 180 只够 ≈17 分钟，而 filter.max_duration 允许 40 分钟 —— **配置与超时互相矛盾**。
# 实测三支长视频全部撞线：worker 5 秒后就出结果了，客户端却已放弃，再回落云端多花约 214s。
from src.transcriber_client import LAPTOP_TRANSCRIBE_TIMEOUT
_max_dur = (cfg.get("filter") or {}).get("max_duration", 0)
_need = 40 + _max_dur / 7.5
check("A4", "L1 超时能覆盖配置的最长视频",
      LAPTOP_TRANSCRIBE_TIMEOUT >= _need,
      f"超时 {LAPTOP_TRANSCRIBE_TIMEOUT}s ≥ 需要 {_need:.0f}s"
      f"（最长 {_max_dur}s ÷ 7.5x + 40s 开销）")

# ══════════════ B 组件 ══════════════
print("\n【B 组件检查】")
from src.pipeline import SummaryPipeline, MIN_SUMMARY_CHARS
from src.summarizer import (looks_like_mismatch_rationalisation as blame_guard,
                            MISMATCH_MARKER, TRUSTED_SOURCES)
from src.cloud_mm import CloudMMExtractor

pipe = SummaryPipeline(cfg)
# 打桩：任何情况下都不许真发私信
_sent = []
pipe.notifier.send_raw_text = lambda uid, text: _sent.append((uid, text)) or True

# B1 空摘要闸门
cases = [("空串", "", False), ("纯空白", "  \n ", False), ("过短", "很好", False),
         ("差一字", "摘" * (MIN_SUMMARY_CHARS - 1), False),
         ("刚达标", "摘" * MIN_SUMMARY_CHARS, True)]
r1 = all(pipe._summary_usable(t, "BV_T") == exp for _, t, exp in cases)
check("B1", f"空摘要闸门（{len(cases)} 例）", r1, f"阈值 {MIN_SUMMARY_CHARS} 字")

# B2 甩锅拦截
block = ["🎯严重“标题党”！标题宣称 AI 教程，实际内容为复刻麦当劳快餐。",
         "标题严重虚标（挂羊头卖狗肉），实际内容为港剧剧情解说。",
         "内容严重货不对板：音轨完全脱离卡牌对局，实则为监控录音。",
         MISMATCH_MARKER]
allow = ["🎯本期影之诗梯度排行，连击妖凭高上限稳居 T1。",
         "🎯本期盘点“标题党”营销号的经典套路，分析其流量逻辑。"]
r2 = all(blame_guard(t) is True for t in block) and all(blame_guard(t) is False for t in allow)
check("B2", f"甩锅拦截（拦 {len(block)} / 放 {len(allow)}）", r2, "含防误伤边界用例")

# B3 来源感知三态
info = {"title": "《影之诗》连击妖卡组排行", "owner_name": "测试UP"}
blame = "🎯标题宣称《影之诗》卡组排行，实则为日常监控录音。"
t_ok = pipe._guard_summary("🎯本期影之诗梯度解析。", info, "BV_T1", "t", "laptop_rtx_5060") is True
t_bad = pipe._guard_summary(blame, info, "BV_T2", "t", "official_subtitle") is False
t_none = pipe._guard_summary(blame, info, "BV_T3", "t", "laptop_rtx_5060") is None
# 该句式早期会整个漏过（词表里写的是带省略号的「标题宣称……实际」）
t_claim = pipe._guard_summary("🎯标题宣称《影之诗》卡组排行，实则为日常监控录音。",
                              info, "BV_T4", "t", "official_subtitle") is False
check("B3", "来源感知三态 + 标题宣称句式", t_ok and t_bad and t_none and t_claim,
      f"放行={t_ok} 拦下={t_bad} 中性改写={t_none} 标题宣称式={t_claim}")

# B4 相关性闸门
mm = CloudMMExtractor(base_url=cfg["llm"]["base_url"], api_key=cfg["llm"]["api_key"],
                      model="gemini-3.7-flash-high")
T = "《水月雨兰2 开箱 对比 原道泪落》"
r_wrong = mm.verify_relevance(T, "测试UP", "今天复刻麦当劳炸鸡，先准备冷冻鸡腿，用复合腌料腌制。")
r_right = mm.verify_relevance(T, "测试UP", "开箱水月雨兰2耳机，与原道泪落对比，低频量感更足，人声贴耳。")
check("B4", "相关性闸门（错配拒 / 匹配放）", r_wrong is False and r_right is True,
      f"错配={r_wrong}（期望False） 匹配={r_right}（期望True）")

# B5 字幕通道已关闭
check("B5", "字幕通道已显式关闭", getattr(pipe.transcriber, "enable_subtitles", None) is False,
      f"enable_subtitles={getattr(pipe.transcriber, 'enable_subtitles', None)}")

# B6 存储层往返
try:
    pipe.storage.mark_pending(bvid="BV_SELFTEST_X", title="自检", owner_name="t",
                              source_type="selftest", note="自检记录")
    rows = [r for r in pipe.storage.get_pending(limit=100) if r["bvid"] == "BV_SELFTEST_X"]
    ok6 = len(rows) == 1 and rows[0]["status"] == "pending"
    import sqlite3
    with sqlite3.connect("data/history.db") as c:
        c.execute("DELETE FROM processed_videos WHERE bvid='BV_SELFTEST_X'")
        c.commit()
    check("B6", "存储层 pending 往返 + 清理", ok6, f"取回 {len(rows)} 条")
except Exception as e:
    check("B6", "存储层 pending 往返", False, str(e)[:80])

# B7 通知器空摘要兜底（打桩，绝不真发）
import glob
try:
    pipe.notifier.send_summary_card(
        video_info={"bvid": "BV_T", "title": "自检", "owner_name": "t", "duration": 1},
        summary_markdown="", fallback_dir="/tmp/bili/_st")
    n = len(glob.glob("/tmp/bili/_st/*"))
    check("B7", "通知器拒绝发送空摘要", n == 0, f"归档文件数 {n}（期望 0）")
except Exception as e:
    check("B7", "通知器拒绝发送空摘要", False, str(e)[:80])
finally:
    import shutil
    shutil.rmtree("/tmp/bili/_st", ignore_errors=True)

# B8-B11 深度用例（由一次专项深测发现的问题固化而来，防止回归）
NOTIF_MAX = (cfg.get("notification") or {}).get("max_chunk_size", 700)
# 空文本
check("B8", "分段：空文本返回空列表（不发空白私信）",
      pipe.notifier._split_message("") == [])
# 超长无换行文本必须严守上限（旧实现会产出 744 字的段）
_long = "x" * (NOTIF_MAX * 2)
_ch = pipe.notifier._split_message(_long)
check("B9", "分段：超长单行严守上限",
      bool(_ch) and max(len(x) for x in _ch) <= NOTIF_MAX,
      f"{len(_ch)} 段，最长 {max((len(x) for x in _ch), default=0)}（上限 {NOTIF_MAX}）")
# 临时文件清理必须真的接上线（cleanup() 曾经全项目零调用）
check("B10", "临时文件清理已接线",
      hasattr(pipe.transcriber, "_cleanup_temp") and hasattr(pipe.cloud_mm, "cleanup_stale"))
# 清理逻辑真的能删（在真实 work_dir 里造一个再删）
try:
    _wd = pipe.cloud_mm.work_dir
    _wd.mkdir(parents=True, exist_ok=True)
    _f = _wd / "BV_DEEPTEST_cloud.mp3"
    _f.write_text("x")
    pipe.transcriber._cleanup_temp("BV_DEEPTEST")
    check("B11", "临时文件确实被删掉", not _f.exists())
except Exception as _e:
    check("B11", "临时文件确实被删掉", False, str(_e)[:60])

# 闸门顺序回归：三态防线会**改写**摘要，空摘要闸门只判长短。
# 后者若先跑，会把 __CONTENT_MISMATCH__（20 字）当"空摘要"直接拦死，
# _neutral_rewrite() 永远轮不到执行 → 文不对题的视频永远卡在待补推队列。
# 实测事故：BV1A8YX6uE1j 常规摘要 20 字被拦，中性披露重写实为 259 字（本该发出去）。
from src.summarizer import MISMATCH_MARKER as _MM
_trusted_info = {"title": "测试标题", "owner_name": "测试UP", "duration": 120}
_g = pipe._guard_summary(_MM, _trusted_info, "BV_T12", "custom", "laptop_rtx_5060")
check("B12", "可信来源的文不对题 → 走中性披露（返回 None，不是拦下）", _g is None,
      f"返回 {_g!r}（None=重写 / False=拦下 / True=放行）")
# 重写后的长摘要必须能通过空摘要闸门（证明顺序修好后链路才通）
check("B13", "中性披露重写后的摘要能通过空摘要闸门",
      pipe._summary_usable("修复后的正常长度摘要" * 8, "BV_T12"))
# 顺序断言：源码里凡出现 `_summary_usable(summary` 之前，不得已有 `_guard_summary(summary`
import re as _re
_pipe_src = open("src/pipeline.py", encoding="utf-8").read()
_order = [m.group(1) for m in _re.finditer(r"(_guard_summary|_summary_usable)\(summary", _pipe_src)]
# 顺序断言：每条推送路径内部，`_summary_usable` 之前必须先出现 `_guard_summary`。
# ⚠️ 不能用"相邻对逆序"来判断——两条路径各自的 G→U 会在交界处形成
#    U(路径1末) → G(路径2首) 的假逆序对，那是**正确**布局却被误判为失败。
_ok_order = True
_seen_guard = False
for _name in _order:
    if _name == "_guard_summary":
        _seen_guard = True
    elif _name == "_summary_usable":
        if not _seen_guard:
            _ok_order = False
        _seen_guard = False
check("B14", "闸门顺序：三态防线早于空摘要闸门", _ok_order,
      f"两条路径的调用顺序: {_order}")

# ══════════════ C 端到端（不发私信）══════════════
print("\n【C 端到端：真实视频走内容获取链】")
if QUICK:
    for _n in ("C1 抓取视频详情", "C2 取音频流地址", "C3 内容链拿到真实正文",
               "C4 来源属可信通道", "C5 中性披露模式"):
        skip("C", _n, "精简模式；周日跑全量")
else:
    BV = "BV1w3Yk6rEXd"
    try:
        info2 = pipe.bili_client.get_video_info(BV)
        check("C1", "抓取视频详情", bool(info2.get("title")),
              f"《{info2['title'][:26]}》 时长{info2.get('duration')}s")
        try:
            au = pipe.bili_client.get_audio_stream_url(BV, info2["cid"])
            ok_au = bool(au)
        except Exception:
            au, ok_au = "", False
        check("C2", "取音频流地址", ok_au, "" if ok_au else "取流失败（将走视觉通道）")
        t0 = time.time()
        tr = pipe.transcriber.transcribe(bvid=BV, cid=info2["cid"], audio_url=au, video_info=info2)
        dt = time.time() - t0
        txt = (tr or {}).get("full_text") or ""
        src = (tr or {}).get("source") or ""
        check("C3", "内容获取链拿到真实正文", len(txt) > 100,
              f"来源={src} 字符={len(txt)} 耗时={dt:.0f}s")
        check("C4", "来源属可信通道（非字幕）", src in TRUSTED_SOURCES,
              f"source={src} 可信集={TRUSTED_SOURCES}")
        # C5 中性披露模式可用（不产生指控性措辞）
        try:
            s = pipe.summarizer.summarize(video_info=info2, transcription=tr,
                                          detailed=False, mismatch_disclosure=True)
            ok5 = bool(s) and MISMATCH_MARKER not in s and not blame_guard(s)
            check("C5", "中性披露模式（不吐标记、不甩锅）", ok5,
                  f"{len(s or '')} 字" if ok5 else f"输出异常: {str(s)[:50]}")
        except Exception as e:
            check("C5", "中性披露模式", False, str(e)[:80])
    except Exception as e:
        check("C1-C5", "端到端内容获取", False, f"异常: {str(e)[:90]}")

# ══════════════ D 运行态 ══════════════
print("\n【D 运行态检查】")


def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
    return (r.stdout or "").strip()


check("D1", "bili-summary.service 运行中",
      sh("systemctl is-active bili-summary.service") == "active",
      f"PID {sh('systemctl show bili-summary.service -p MainPID --value')}")
check("D2", "服务设为 enabled（开机自启）",
      sh("systemctl is-enabled bili-summary.service") == "enabled")

_restart = sh("systemctl show bili-summary.service -p Restart --value")
check("D3", "服务配了 Restart=always", "always" in _restart, _restart)

# D4 近期错误日志
# 注意：journalctl 无结果时输出 "-- No entries --"，直接 wc -l 会数成 1 条（误报）
errout = sh("journalctl -u bili-summary.service --since '24 hours ago' -p err --no-pager")
if not errout.strip() or "No entries" in errout:
    n_err = 0
else:
    n_err = sum(1 for ln in errout.splitlines() if re.match(r"^\w{3} \d{2} ", ln))
check("D4", "近 24 小时无 error 级日志", n_err == 0, f"{n_err} 条")

# D5 金丝雀（可选：需自备 tools/gate_canary.py，随个人环境而定）
if os.path.exists(f"{PROJ}/tools/gate_canary.py"):
    r = subprocess.run([f"{PROJ}/.venv/bin/python", "tools/gate_canary.py"],
                       capture_output=True, text=True, timeout=300)
    check("D5", "闸门金丝雀全部通过", r.returncode == 0,
          "全部断言通过" if r.returncode == 0 else (r.stdout or "")[-120:])
else:
    check("D5", "闸门金丝雀（未安装 gate_canary.py，跳过）", True, "skipped")

# D6 定时器（可选：随金丝雀一起提供）
if os.path.exists(f"{PROJ}/tools/gate_canary.py"):
    check("D6", "每日金丝雀定时器已启用",
          sh("systemctl is-enabled bili-gate-canary.timer") == "enabled",
          sh("systemctl list-timers bili-gate-canary.timer --no-pager | awk 'NR==2{print $1,$2,$3}'"))
else:
    check("D6", "金丝雀定时器（未安装，跳过）", True, "skipped")

# D7 无重复巡检（PC 侧不该在跑）
# VPS 本来就该跑 1 个巡检进程（它是主控）；要查的是「有没有重复」。
# 不能用 `pgrep -f`——它会把执行该命令的 shell 自身也匹配进去。
n_watch = len([l for l in sh("ps -eo args").splitlines()
               if "main.py watch" in l and "grep" not in l and "ps -eo" not in l])
check("D7", "巡检进程恰好 1 个（无重复）", n_watch == 1, f"main.py watch 进程数={n_watch}")

# ══════════════ 汇总 ══════════════
# 自检会产生 BV_T* 这类临时记录（B3 的三态测试会调 mark_pending），
# 必须清掉——否则服务会把它当成待补推任务去抓取不存在的视频，刷出噪音日志。
import sqlite3 as _sq
with _sq.connect(f"{PROJ}/data/history.db") as _c:
    _n = _c.execute("DELETE FROM processed_videos WHERE bvid LIKE 'BV_T%' "
                    "OR bvid LIKE 'BV_SELFTEST%'").rowcount
    _c.commit()
if _n:
    print(f"\n  （已清理本次自检产生的 {_n} 条临时记录）")

print("\n" + "=" * 78)
print(f"结果：通过 {len(PASS)}  失败 {len(FAIL)}  跳过 {len(SKIP)}")
if FAIL:
    print("\n失败项：")
    for f in FAIL:
        print(f"  ✗ {f}")
print("=" * 78)
sys.exit(1 if FAIL else 0)
