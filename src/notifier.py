import time
import json
import uuid
from typing import Dict, Any, List, Union
from pathlib import Path
import requests

# 多段消息的前缀「【B站视频总结 · 第 N/M 部分】\n」占用约 20 字。
# 切分时必须预留这段空间，否则加上前缀就会超出 max_chunk_size。
_PART_PREFIX_ALLOWANCE = 24

class BiliNotifier:
    SEND_MSG_URL = "https://api.vc.bilibili.com/web_im/v1/web_im/send_msg"

    def __init__(
        self,
        sessdata: str,
        bili_jct: str,
        sender_uid: int,
        receiver_uids: Union[List[int], int],
        max_chunk_size: int = 700,
        send_interval: float = 1.5
    ):
        self.sessdata = sessdata
        self.bili_jct = bili_jct
        self.sender_uid = int(sender_uid) if sender_uid else 0

        if isinstance(receiver_uids, list):
            self.receiver_uids = [int(u) for u in receiver_uids if u]
        elif receiver_uids:
            self.receiver_uids = [int(receiver_uids)]
        else:
            self.receiver_uids = []

        self.max_chunk_size = max_chunk_size
        self.send_interval = send_interval

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://message.bilibili.com/",
            "Origin": "https://message.bilibili.com"
        })
        if self.sessdata:
            self.session.cookies.set("SESSDATA", self.sessdata, domain=".bilibili.com")
        if self.bili_jct:
            self.session.cookies.set("bili_jct", self.bili_jct, domain=".bilibili.com")
        if self.sender_uid:
            self.session.cookies.set("DedeUserID", str(self.sender_uid), domain=".bilibili.com")

    def _split_message(self, text: str) -> List[str]:
        if not text:
            # 空文本返回空列表——绝不让"空消息"进入发送流程
            # （旧行为是返回 ['']，会给用户发一条空白私信）
            return []
        if len(text) <= self.max_chunk_size:
            return [text]

        # 多段时每段会被加上「【B站视频总结 · 第 N/M 部分】」前缀（约 20 字）。
        # 若按 max_chunk_size 切分，加上前缀就会**超出上限**（实测 700 → 720）。
        # 先按原上限试切；若确实分成多段，再用扣掉前缀的预算重切一次。
        chunks = self._split_to_chunks(text, self.max_chunk_size)
        if len(chunks) > 1:
            chunks = self._split_to_chunks(text, self.max_chunk_size - _PART_PREFIX_ALLOWANCE)
            total = len(chunks)
            return [f"【B站视频总结 · 第 {i}/{total} 部分】\n{c}"
                    for i, c in enumerate(chunks, 1)]
        return chunks

    def _split_to_chunks(self, text: str, budget: int) -> List[str]:
        """按行切分，保证每段不超过 budget。

        ⚠️ 超长单行必须**反复**切，不能只切一刀：旧实现把 `line[:budget]` 塞进一段、
        把剩余部分留在 current_chunk 里就不管了——剩余部分本身就可能远超预算
        （实测 1400 字无换行文本会产出 744 字的段，超出上限）。
        """
        lines = text.split("\n")
        chunks, current_chunk, current_len = [], [], 0

        for line in lines:
            while len(line) > budget:
                if current_chunk:
                    chunks.append("\n".join(current_chunk))
                    current_chunk, current_len = [], 0
                chunks.append(line[:budget])
                line = line[budget:]

            line_len = len(line) + 1
            if current_len + line_len > budget:
                if current_chunk:
                    chunks.append("\n".join(current_chunk))
                current_chunk, current_len = [line], line_len
            else:
                current_chunk.append(line)
                current_len += line_len

        if current_chunk:
            chunks.append("\n".join(current_chunk))
        return chunks

    def send_raw_text(self, receiver_uid: int, text: str) -> bool:
        if not self.sessdata or not self.bili_jct:
            raise ValueError("未配置 B站 SESSDATA 或 bili_jct (csrf)，无法发送私信")
        if not receiver_uid:
            raise ValueError("未指定有效 receiver_uid，无法发送私信")

        dev_id = str(uuid.uuid4()).upper()
        now_ts = int(time.time())

        payload = {
            "msg[sender_uid]": self.sender_uid,
            "msg[receiver_id]": int(receiver_uid),
            "msg[receiver_type]": 1,
            "msg[msg_type]": 1,
            "msg[msg_status]": 0,
            "msg[content]": json.dumps({"content": text}, ensure_ascii=False),
            "msg[new_face_version]": 0,
            "msg[timestamp]": now_ts,
            "msg[dev_id]": dev_id,
            "csrf": self.bili_jct,
            "csrf_token": self.bili_jct
        }

        resp = self.session.post(self.SEND_MSG_URL, data=payload, timeout=15)
        res = resp.json()

        if res.get("code") == 0:
            return True
        else:
            err_msg = res.get("message") or res.get("msg")
            code = res.get("code")
            raise RuntimeError(f"B站私信发送失败 [code={code}]: {err_msg}")

    def send_summary_card(self, video_info: Dict[str, Any], summary_markdown: str,
                          fallback_dir: str = "summaries", source_note: str = ""):
        """推送总结卡片。

        ``source_note`` 标注这份总结建立在什么内容之上（本机GPU转写 / B站AI字幕 /
        云端ASR / 云端视觉描述）——让用户一眼分辨真伪，也是空壳不再悄悄出现的长效手段。
        """
        out_dir = Path(fallback_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        # 兜底防线：绝不发送「只有标题头、没有正文」的空消息。
        # 历史事故：摘要器返回空字符串时，代码未做检查，用户收到 6 条有头无身的噪音。
        if not (summary_markdown or "").strip():
            print("[!] 摘要正文为空，取消本次推送（不发只有标题头的空消息）")
            return
        safe_title = "".join(c for c in video_info.get("title", "untitled") if c not in r'\/:*?"<>|')[:50]
        md_file = out_dir / f"{video_info.get('bvid')}_{safe_title}.md"
        src_line = f"- **内容来源**: {source_note}\n" if source_note else ""

        full_doc = f"# {video_info.get('title')}\n\n"
        full_doc += f"- **UP 主**: {video_info.get('owner_name')}\n"
        full_doc += f"- **链接**: https://www.bilibili.com/video/{video_info.get('bvid')}\n"
        full_doc += f"- **时长**: {video_info.get('duration')} 秒\n"
        full_doc += src_line + "\n"
        full_doc += "---\n\n"
        full_doc += summary_markdown

        with open(md_file, "w", encoding="utf-8") as f:
            f.write(full_doc)

        print(f"[+] 总结内容已本地归档: {md_file}")

        src_badge = f"内容来源: {source_note}\n" if source_note else ""
        header = (f"🎬【视频速读总结推送】\n《{video_info.get('title')}》\n"
                  f"UP主: {video_info.get('owner_name')}\n"
                  f"直达链接: https://b23.tv/{video_info.get('bvid')}\n"
                  f"{src_badge}"
                  f"------------------------\n")
        full_message = header + summary_markdown
        chunks = self._split_message(full_message)

        if not self.receiver_uids:
            print("[!] 未配置任何 receiver_uids，跳过私信推送")
            return

        print(f"[*] 准备向 {len(self.receiver_uids)} 个账号推送私信 (每人 {len(chunks)} 条分段)...")
        for target_uid in self.receiver_uids:
            print(f"  --> 正在推送至 UID: {target_uid} ...")
            failed = []
            for idx, chunk in enumerate(chunks, 1):
                try:
                    self.send_raw_text(target_uid, chunk)
                    print(f"    [✓] 分段 {idx}/{len(chunks)} 送达")
                except Exception as e:
                    # 不再静默跳过。B站有反垃圾限制（code=21047「对方主动回复或关注你前，
                    # 最多发送1条消息」），多段推送的第 2 段会被拒——此时用户只看得到
                    # 半截总结。必须让它在日志里显眼，而不是一行带过。
                    failed.append((idx, str(e)))
                    print(f"    [✗] 分段 {idx}/{len(chunks)} 失败: {e}")
                if idx < len(chunks):
                    time.sleep(self.send_interval)
            if failed:
                print(f"  [!!!] UID {target_uid} 有 {len(failed)}/{len(chunks)} 段未送达"
                      f" —— 用户可能只看到部分内容")
                for idx, msg in failed:
                    if "21047" in msg:
                        print(f"        · 分段 {idx}：被 B站反垃圾限制拦下"
                              f"（对方未回复/关注前只允许发 1 条）。"
                              f"对策：把总结压到单条可达的长度，或等用户回复后再补发。")
            time.sleep(self.send_interval)
