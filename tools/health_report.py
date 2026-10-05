#!/usr/bin/env python3
"""运行态体检：把各功能模块的真实运行证据汇总出来（只读，不改动任何东西）。"""
import datetime
import os
import re
import sqlite3
import subprocess
import sys

PROJ = "/root/bili-summary-stream"
sys.path.insert(0, PROJ)
os.chdir(PROJ)

def sh(cmd, t=60):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t)
    return (r.stdout or "").strip()

print("=" * 80)
print("BiliSummaryStream 运行态体检 · " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
print("=" * 80)

# ── 1 服务 ──
print("\n【1】服务进程")
print(f"  状态      : {sh('systemctl is-active bili-summary.service')} / {sh('systemctl is-enabled bili-summary.service')}")
pid = sh("systemctl show bili-summary.service -p MainPID --value")
print(f"  PID       : {pid}")
print(f"  启动时长  : {sh(f'ps -o etime= -p {pid}').strip()}")
rss = sh(f"ps -o rss= -p {pid}").strip()
if rss.isdigit():
    print(f"  内存占用  : {int(rss)/1024:.1f} MB")
print(f"  重启策略  : {sh('systemctl show bili-summary.service -p Restart --value')}")
print(f"  重启次数  : {sh('systemctl show bili-summary.service -p NRestarts --value')}")

# ── 2 历史库 ──
print("\n【2】历史库 data/history.db")
c = sqlite3.connect("data/history.db")
c.row_factory = sqlite3.Row
tot = c.execute("SELECT COUNT(*) FROM processed_videos").fetchone()[0]
print(f"  总记录    : {tot} 条")
print("  按状态    :")
for r in c.execute("SELECT COALESCE(status,'(空)') s, COUNT(*) n FROM processed_videos GROUP BY s ORDER BY n DESC"):
    print(f"      {r['s']:12s} {r['n']:4d}")
print("  按内容来源（note 列）:")
for r in c.execute("""SELECT COALESCE(note,'(无)') k, COUNT(*) n FROM processed_videos
                      GROUP BY k ORDER BY n DESC LIMIT 8"""):
    print(f"      {str(r['k'])[:34]:36s} {r['n']:4d}")

# ── 3 最近推送活动 ──
print("\n【3】最近 24 小时的推送活动（按来源）")
day_ago = int(datetime.datetime.now().timestamp()) - 86400
rows = c.execute("SELECT note, processed_at, title FROM processed_videos WHERE processed_at > ? ORDER BY processed_at DESC", (day_ago,)).fetchall()
print(f"  24 小时内处理: {len(rows)} 条")
for r in rows[:12]:
    t = datetime.datetime.fromtimestamp(r["processed_at"]).strftime("%m-%d %H:%M")
    print(f"      {t}  {str(r['note'] or '(无来源标注)')[:16]:18s} 《{str(r['title'])[:30]}》")

# ── 4 日志：巡检与降级链 ──
print("\n【4】daemon.log 最近活动")
log = "daemon.log"
if os.path.exists(log):
    txt = open(log, encoding="utf-8", errors="ignore").read()
    tail = txt[-400000:]
    for pat, label in [
        (r"定时巡检触发", "定时巡检触发次数"),
        (r"检测到笔记本 RTX 5060 在线", "★ GPU 路由成功（本机转写）"),
        (r"无缝启动云端自愈降级链路", "云端降级启动次数"),
        (r"云端 ASR", "云端 ASR 转写"),
        (r"云端视觉", "云端视觉描述"),
        (r"内容一致性", "内容一致性判定"),
        (r"空摘要闸门", "空摘要闸门拦截"),
        (r"字幕串台拦截", "字幕串台拦截"),
        (r"收到配置指令", "收到用户私信指令"),
        (r"待补推", "待补推相关"),
    ]:
        n = len(re.findall(pat, tail))
        print(f"      {label:24s} {n:5d}")

# ── 5 错误与异常 ──
print("\n【5】错误与异常（最近日志尾部）")
for pat, label in [(r"\[!\] (?!.*兜底)", "警告/失败"), (r"Traceback", "堆栈异常"),
                   (r"401|403", "鉴权失败"), (r"ConnectionError|超时|timed out", "网络问题")]:
    n = len(re.findall(pat, tail))
    print(f"      {label:12s} {n:5d}")
errs = sh("journalctl -u bili-summary.service --since '24 hours ago' -p err --no-pager")
n_err = 0 if (not errs.strip() or "No entries" in errs) else sum(1 for l in errs.splitlines() if re.match(r"^\w{3} \d{2} ", l))
print(f"      systemd error 级日志  {n_err:5d}")

# ── 6 私信指令监听 ──
print("\n【6】私信指令监听（bot_listener）")
n_cmd = len(re.findall(r"收到配置指令", tail))
n_reply = len(re.findall(r"已成功回复", tail))
print(f"      收到的指令   : {n_cmd}")
print(f"      已回复       : {n_reply}")
last_cmd = re.findall(r"收到配置指令】来自 UID (\d+): (.{0,30})", tail)
if last_cmd:
    print(f"      最近一条     : UID {last_cmd[-1][0]} — {last_cmd[-1][1].strip()}")

# ── 7 防线与体检 ──
print("\n【7】防线与体检")
for f, label in [("data/canary_state.json", "金丝雀最近一次"),
                 ("data/selftest_state.json", "自检最近一次")]:
    try:
        import json
        d = json.load(open(f, encoding="utf-8"))
        print(f"      {label:14s}: {d.get('status')}  {d.get('last_run')}")
    except Exception as e:
        print(f"      {label:14s}: 读取失败 {e}")
for u in ("bili-gate-canary.timer", "bili-self-test.timer"):
    nx = sh(f"systemctl list-timers {u} --no-pager | awk 'NR==2{{print $1,$2,$3}}'")
    print(f"      {u:26s}: {sh(f'systemctl is-enabled {u}')}  下次 {nx}")

# ── 8 关键配置 ──
print("\n【8】关键开关")
import yaml
cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
cm = cfg.get("cloud_mm", {})
print(f"      云端多模态      : enabled={cm.get('enabled')}  model={cm.get('model')}")
print(f"      字幕通道        : {cm.get('subtitle_channel')}（False=已关闭）")
print(f"      待补推通知      : {cfg.get('pending_notice')}")
print(f"      监控账号数      : {len(cfg.get('profiles', {}))}")
for uid, p in (cfg.get("profiles") or {}).items():
    print(f"          · {p.get('name', uid)}: 关键词 {len(p.get('keywords', []))} 个 / UP {len(p.get('up_mids', []))} 个")
print(f"      筛选阈值        : 播放≥{cfg.get('filter',{}).get('min_play')} 时长{cfg.get('filter',{}).get('min_duration')}-{cfg.get('filter',{}).get('max_duration')}s")

c.close()
print("\n" + "=" * 80)
