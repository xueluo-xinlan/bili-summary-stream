#!/usr/bin/env python3
"""B站推送全量复核工具（v3 判官）。

用途：从 B站私信历史取回**当时真正发出去的推送原文**，逐条判定
「正文实际内容 ↔ 标题所指视频」是否一致，输出张冠李戴清单。

用法：
    python tools/review_pushes.py                # 全量复核
    python tools/review_pushes.py --limit 20     # 只查最近 20 条
    python tools/review_pushes.py --json out.json

── 三个已踩过的坑（务必保留这些处理）──────────────────────────────
1. **取信窗口**：`fetch_session_msgs` 的 size 必须给 600。
   给 300 时返回正好 300 条（被截断），会漏掉历史推送。
   返回值 < size 才说明取全了。

2. **按推送头分组**：推送被 `_split_message` 切成 ≤700 字的多段，续段不含标题头。
   若不分组，续段里的书名会被误读成标题。分组时遇到配置面板/更正说明等
   非推送消息必须收尾，否则会把它们并进上一条推送。

3. **判官必须给足 max_tokens**：推理型模型会把预算耗在思考上，
   给 8 个 token 会返回空串，而 `"".startswith("是") == False`
   会被误统计成「判不符」——曾因此产出一份完全失真的统计。
   空响应要显式记为「未判定」，绝不能当成结论。
──────────────────────────────────────────────────────────────
"""
import argparse
import datetime
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from src.config_loader import load_config
from src.cloud_mm import CloudMMExtractor

HEADS = ("🎬【视频速读总结推送】", "⚡【精选动态速读】", "🎬【精细深度拆解】",
         "🎬【专属定制总结推送")
STOP_PREFIX = ("🔧", "⏳", "⚙️", "🤖", "📋", "🛠️", "✅", "❌", "♻️", "🔄")
STOP_WORDS = ("专属配置面板", "修改指令指南", "已成功回复", "待补推", "暂缓推送", "修正重推")

# ⚠️ 坑 4：推送头前面会被 `_split_message` 压一行**分段前缀**
#    【B站视频总结 · 第 1/2 部分】
# 直接 `c.startswith(HEADS)` 会被这行挡住 → 整个专属定制类（多为多段）全军覆没。
# 实测后果：一次 117 条的"全量"复核，实际只覆盖了「视频速读」一类，
# 而私信窗口里真实存在的推送是 227 条（差的那一半全是专属定制）。
_CHUNK_PREFIX = re.compile(r"^【B站视频总结\s*·\s*第\s*\d+/\d+\s*部分】[ \t]*")


def _strip_chunk_prefix(text: str) -> str:
    """去掉分段前缀，让推送头回到行首。"""
    return _CHUNK_PREFIX.sub("", text.lstrip())


def _push_header_index(text: str):
    """推送头所在的**行号**（0 起）；找不到返回 None。

    ⚠️ 坑 5：不能要求推送头出现在**第一行**。实测有约 30 条推送（162 中有）
    在推送头之前还压着别的行，只认行首会静默漏掉它们。
    限定只扫前 4 行，避免把正文里引用推送头的段落误判成新推送。
    """
    for i, line in enumerate(text.split("\n")[:4]):
        s = line.strip()
        if any(s.startswith(h) for h in HEADS):
            return i
    return None


JUDGE = """你是内容审核助手。判断**正文实际讲述的内容**是否属于**标题所指的那支视频**。

【标题】{title}
【UP主】{owner}
【正文】(可能被截断，以你看到的为准)
{body}

⚠️ 最关键的一条：正文里可能夹着**对标题的评论**，例如
   「标题党」「挂羊头卖狗肉」「货不对板」「标题与内容脱节」「与标题不符」
   「标题虚标」「实则为……」。
   **必须完全忽略这类评论性语句**，只抽取正文**实际讲述的内容**是什么。
   反例：正文写「标题宣称影之诗卡组排行，实则为日常监控录音」
        → 正文主题应写「日常监控录音」，绝不能写成「吐槽标题」或「打假」。

请先抽主题再判定：
· MATCH    —— 两者属于同一内容领域/同一话题（正文在批评、打假、吐槽该话题也算 MATCH，
               但前提是**去掉评论句后剩下的实质内容**仍属于该话题）。
· MISMATCH —— 去掉评论句后，实质内容属于**完全不同的领域**
               （例：标题讲游戏卡组、实质内容是麦当劳制作）。
· NO_BODY  —— 正文没有实质内容。

只输出 JSON，不要其他文字，不要代码块：
{{"title_topic": "≤12字", "content_topic": "≤12字", "verdict": "MATCH|MISMATCH|NO_BODY"}}"""

REVIEW = """两个主题有没有可能**同属同一个视频**？
例：「AI大模型周报」与「大模型架构量化」→ 可能。
例：「影之诗卡组指南」与「麦当劳炸鸡制作」→ 不可能。
【标题主题】{t}
【正文主题】{c}
只回答两个字：可能 或 不可能。"""


def fetch_pushes(cfg, cookies, headers, sender):
    """取回私信历史并按推送分组。"""
    url = "https://api.vc.bilibili.com/svr_sync/v1/svr_sync/fetch_session_msgs"
    out = []
    for uid in cfg["notification"]["receiver_uids"]:
        r = requests.get(url, headers=headers, cookies=cookies,
                         params={"talker_id": uid, "session_type": 1, "size": 600},
                         timeout=30).json()
        msgs = [m for m in (r.get("data") or {}).get("messages") or []
                if int(m.get("sender_uid") or 0) == sender]
        msgs.sort(key=lambda m: m.get("timestamp", 0))
        cur = None
        for m in msgs:
            try:
                c = json.loads(str(m.get("content"))).get("content", "")
            except Exception:
                c = str(m.get("content"))
            c = _strip_chunk_prefix(c)
            hi = _push_header_index(c)
            if hi is not None:
                if cur:
                    out.append(cur)
                head_block = "\n".join(c.split("\n")[hi:])
                tm = re.search(r"《(.+?)》", head_block, re.S)
                om = re.search(r"UP主[:：]\s*([^|｜\n]+)", head_block)
                bv = re.search(r"b23\.tv/(BV\w+)", head_block)
                cur = {"uid": uid, "ts": m.get("timestamp", 0),
                       "title": tm.group(1).strip() if tm else "?",
                       "owner": om.group(1).strip() if om else "?",
                       "bvid": bv.group(1) if bv else "?", "body": head_block}
            elif cur is not None:
                if c.startswith(STOP_PREFIX) or any(w in c for w in STOP_WORDS):
                    out.append(cur)
                    cur = None
                else:
                    cur["body"] += "\n" + c
        if cur:
            out.append(cur)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只查最近 N 条")
    ap.add_argument("--json", default="/tmp/bili/review_v3.json")
    ap.add_argument("--votes", type=int, default=3)
    args = ap.parse_args()

    cfg = load_config("config.yaml")
    b = cfg["bilibili"]
    sender = int(b["sender_uid"])
    cookies = {"SESSDATA": b["sessdata"], "bili_jct": b["bili_jct"],
               "DedeUserID": str(sender)}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0",
               "Referer": "https://message.bilibili.com/"}

    pushes = fetch_pushes(cfg, cookies, headers, sender)

    # 空壳消息（只有标题头、没有正文）单独归类，不算张冠李戴
    empty = [p for p in pushes
             if len(p["body"][p["body"].find("------------------------") + 24:].strip()) < 30]
    real = [p for p in pushes if p not in empty]
    if args.limit:
        real = real[-args.limit:]
    print(f"取回推送 {len(pushes)} 条：有正文 {len(real)} 条，空壳 {len(empty)} 条")
    if empty:
        print(f"  ⚠ 空壳消息 {len(empty)} 条（另案处理，不计入一致率）")
    print()

    llm = cfg["llm"]
    mm = CloudMMExtractor(base_url=llm.get("base_url"), api_key=llm.get("api_key"),
                          model="gemini-3.7-flash-high")

    def call(prompt, max_tokens):
        try:
            return (mm._chat([{"type": "text", "text": prompt}],
                             max_tokens=max_tokens, temperature=0.0) or "").strip()
        except Exception:
            return ""

    def judge(p):
        vs, topics = [], []
        for _ in range(args.votes):
            raw = call(JUDGE.format(title=p["title"][:90], owner=p["owner"][:30],
                                    body=p["body"][:2400]), 220)
            m = re.search(r"\{.*\}", raw, re.S)
            if not m:
                vs.append(None)
                continue
            try:
                d = json.loads(m.group(0))
            except Exception:
                vs.append(None)
                continue
            topics.append((d.get("title_topic", ""), d.get("content_topic", "")))
            if str(d.get("verdict", "")).upper() == "MISMATCH":
                rv = call(REVIEW.format(t=d.get("title_topic", ""),
                                        c=d.get("content_topic", "")), 12)
                vs.append(False if rv.startswith("可能") else True)
            else:
                vs.append(False)
            time.sleep(0.1)
        valid = [x for x in vs if x is not None]
        if not valid:
            return None, topics
        return (sum(valid) * 2 > len(valid)), topics

    bad, ok, unjudged = [], 0, 0
    for i, p in enumerate(real, 1):
        verdict, topics = judge(p)
        if verdict is None:
            unjudged += 1
        elif verdict:
            bad.append({**p, "topics": topics})
            t = datetime.datetime.fromtimestamp(p["ts"]).strftime("%m-%d %H:%M")
            print(f"  ✗ [{i}] {t} {p['bvid']} 《{p['title'][:34]}》")
            if topics:
                print(f"        标题主题={topics[0][0]} ｜ 正文主题={topics[0][1]}")
        else:
            ok += 1
        if i % 10 == 0:
            print(f"  ...{i}/{len(real)}（相符 {ok} 不符 {len(bad)}）", flush=True)

    total = ok + len(bad)
    print("\n" + "=" * 78)
    print(f"复核完成：相符 {ok}，张冠李戴 {len(bad)}，未判定 {unjudged}，合计 {total}")
    if total:
        print(f"★ 张冠李戴比例 = {len(bad)/total*100:.1f}%（未判定不计入分母）")
    print("=" * 78)

    with open(args.json, "w", encoding="utf-8") as f:
        json.dump([{k: p[k] for k in ("bvid", "uid", "ts", "title", "owner", "body")}
                   for p in bad], f, ensure_ascii=False, indent=2)
    print(f"明细 -> {args.json}")


if __name__ == "__main__":
    main()
