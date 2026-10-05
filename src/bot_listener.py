import time
import re
import json
import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional, Set
import requests

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"

class BotListener:
    FETCH_MSGS_URL = "https://api.vc.bilibili.com/svr_sync/v1/svr_sync/fetch_session_msgs"
    AT_MSGS_URL = "https://api.bilibili.com/x/msgfeed/at"

    def __init__(self, pipeline, config: Dict[str, Any]):
        self.pipeline = pipeline
        self.config = config
        self.bili_cfg = config.get("bilibili", {})
        self.profiles = config.get("profiles", {})
        self.listener_cfg = config.get("interactive_listener", {})
        self.poll_interval = self.listener_cfg.get("poll_interval_seconds", 6)

        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://message.bilibili.com/"
        }
        self.cookies = {
            "SESSDATA": self.bili_cfg.get("sessdata", ""),
            "bili_jct": self.bili_cfg.get("bili_jct", ""),
            "DedeUserID": str(self.bili_cfg.get("sender_uid", ""))
        }

        self.processed_msg_keys: Set[int] = set()
        self.processed_at_ids: Set[int] = set()
        self._init_watermark()

    def _init_watermark(self):
        targets = self.config.get("notification", {}).get("receiver_uids", [])
        bot_uid = self.bili_cfg.get("sender_uid", 0)

        for uid in targets:
            msgs = self._fetch_messages(uid, size=50)
            for m in msgs:
                m_key = m.get("msg_key")
                if m_key and m.get("sender_uid") == bot_uid:
                    self.processed_msg_keys.add(m_key)

        if self.listener_cfg.get("listen_at_comments", True):
            ats = self._fetch_at_messages()
            for at in ats:
                at_id = at.get("id")
                if at_id:
                    self.processed_at_ids.add(at_id)
        print(f"[*] 监听基准线就绪，已标记机器人已发历史消息 {len(self.processed_msg_keys)} 条")

    def _fetch_messages(self, talker_uid: int, size: int = 50) -> List[Dict[str, Any]]:
        params = {
            "talker_id": talker_uid,
            "session_type": 1,
            "size": size
        }
        try:
            r = requests.get(self.FETCH_MSGS_URL, headers=self.headers, cookies=self.cookies, params=params, timeout=10)
            res = r.json()
            if res.get("code") == 0:
                return res.get("data", {}).get("messages", [])
        except Exception as e:
            print(f"[!] 拉取 UID {talker_uid} 私信异常: {e}")
        return []

    def _fetch_at_messages(self) -> List[Dict[str, Any]]:
        try:
            r = requests.get(self.AT_MSGS_URL, headers=self.headers, cookies=self.cookies, timeout=10)
            res = r.json()
            if res.get("code") == 0:
                return res.get("data", {}).get("items", [])
        except Exception as e:
            print(f"[!] 拉取 @我的通知 异常: {e}")
        return []

    def _save_profiles_to_disk(self):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                full_cfg = yaml.safe_load(f)
            full_cfg["profiles"] = self.profiles
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.safe_dump(full_cfg, f, allow_unicode=True, sort_keys=False)
            print(f"[✓] 账号配置已持久化保存至 {CONFIG_PATH}")
        except Exception as e:
            print(f"[!] 持久化 config.yaml 异常: {e}")

    def _handle_direct_command(self, talker_uid: int, text: str) -> Optional[str]:
        clean = text.strip()
        uid_str = str(talker_uid)

        if uid_str not in self.profiles:
            self.profiles[uid_str] = {
                "name": f"用户_{talker_uid}",
                "keywords": [],
                "up_mids": [],
                "requirements": "核心主旨与深度干货提炼"
            }
        prof = self.profiles[uid_str]
        name = prof.get("name", f"用户_{talker_uid}")

        if clean in ["查看设置", "当前设置", "我的设置", "查看配置", "我的配置", "配置", "帮助"]:
            kw_str = "、".join(prof.get("keywords", [])) or "暂无"
            up_str = "、".join(str(m) for m in prof.get("up_mids", [])) or "暂无"
            req_str = prof.get("requirements", "暂无")
            reply = (
                f"⚙️【{name} · 专属配置面板】\n"
                f"------------------------\n"
                f"📌 监控关键词：{kw_str}\n"
                f"📺 监控UP主(UID)：{up_str}\n"
                f"💡 总结风格偏好：{req_str}\n"
                f"------------------------\n"
                f"🛠️ 修改指令指南：\n"
                f"• 设置关键词 词1, 词2, 词3\n"
                f"• 添加关键词 词名 (或 +关键词 词名)\n"
                f"• 删除关键词 词名 (或 -关键词 词名)\n"
                f"• 设置风格 你的专属总结要求\n"
                f"• 监控UP 123456\n"
                f"• 取消监控UP 123456\n"
                f"• 设置分区 分区名\n"
                f"• 搜索 关键词 (即时速览)\n"
                f"• 直接发 BV号 / b23.tv 链接，我会即时精读"
            )
            return reply

        m_set_kw = re.match(r'^(?:设置|修改)关键词\s*[:：]?\s*(.+)$', clean, re.IGNORECASE)
        if m_set_kw:
            raw_kws = m_set_kw.group(1).strip()
            kws = [k.strip() for k in re.split(r'[,，、;；\s]+', raw_kws) if k.strip()]
            prof["keywords"] = kws
            self._save_profiles_to_disk()
            return f"✅【配置已更新】\n你的专属监控关键词已设置为：\n{'、'.join(kws)}"

        m_add_kw = re.match(r'^(?:添加|\+)\s*关键词\s*[:：]?\s*(.+)$', clean, re.IGNORECASE)
        if m_add_kw:
            raw_kws = m_add_kw.group(1).strip()
            new_kws = [k.strip() for k in re.split(r'[,，、;；\s]+', raw_kws) if k.strip()]
            curr = prof.get("keywords", [])
            for k in new_kws:
                if k not in curr:
                    curr.append(k)
            prof["keywords"] = curr
            self._save_profiles_to_disk()
            return f"✅【关键词已添加】\n当前完整关键词列表：\n{'、'.join(curr)}"

        m_del_kw = re.match(r'^(?:删除|\-)\s*关键词\s*[:：]?\s*(.+)$', clean, re.IGNORECASE)
        if m_del_kw:
            raw_kws = m_del_kw.group(1).strip()
            del_kws = [k.strip() for k in re.split(r'[,，、;；\s]+', raw_kws) if k.strip()]
            curr = prof.get("keywords", [])
            curr = [k for k in curr if k not in del_kws]
            prof["keywords"] = curr
            self._save_profiles_to_disk()
            return f"✅【关键词已移除】\n当前完整关键词列表：\n{'、'.join(curr) or '空'}"

        m_style = re.match(r'^(?:设置|修改)(?:风格|偏好|要求)\s*[:：]?\s*(.+)$', clean)
        if m_style:
            new_req = m_style.group(1).strip()
            prof["requirements"] = new_req
            self._save_profiles_to_disk()
            return f"✅【总结风格已更新】\n后续为你推送的视频将重点遵循：\n“{new_req}”"

        m_add_up = re.match(r'^(?:添加|监控|\+)\s*UP\s*[:：]?\s*(\d+)$', clean, re.IGNORECASE)
        if m_add_up:
            up_mid = int(m_add_up.group(1))
            curr = prof.get("up_mids", [])
            if up_mid not in curr:
                curr.append(up_mid)
            prof["up_mids"] = curr
            self._save_profiles_to_disk()
            return f"✅【UP主监控已添加】\n已将 UID {up_mid} 加入你的专属巡检名单"

        m_del_up = re.match(r'^(?:删除|取消监控|\-)\s*UP\s*[:：]?\s*(\d+)$', clean, re.IGNORECASE)
        if m_del_up:
            up_mid = int(m_del_up.group(1))
            curr = prof.get("up_mids", [])
            curr = [m for m in curr if m != up_mid]
            prof["up_mids"] = curr
            self._save_profiles_to_disk()
            return f"✅【UP主监控已移除】\n已取消对 UID {up_mid} 的专属监控"

        m_set_part = re.match(r'^(?:设置|修改)分区\s*[:：]?\s*(.+)$', clean)
        if m_set_part:
            raw_parts = m_set_part.group(1).strip()
            parts = [p.strip() for p in re.split(r'[,，、;；\s]+', raw_parts) if p.strip()]
            prof["partitions"] = parts
            self._save_profiles_to_disk()
            return f"✅【监控分区已更新】\n你的专属监控分区已设定为：\n{'、'.join(parts)}"

        m_search_push = re.match(r'^(?:即时推送|推送最新|搜索推送|搜索)\s*[:：]?\s*(.+)$', clean)
        if m_search_push:
            query = m_search_push.group(1).strip()
            from src.bili_api import BiliClient
            part_map = BiliClient.PARTITION_MAP

            matched_rid = None
            for p_name, rid in part_map.items():
                if query.startswith(p_name) or p_name in query:
                    matched_rid = rid
                    break

            try:
                self.pipeline.notifier.send_raw_text(
                    talker_uid,
                    f"🔍 正在为你即时检索【{query}】的高质量视频并提炼速读，请稍候..."
                )
            except Exception:
                pass

            vlist = []
            if matched_rid:
                vlist = self.pipeline.bili_client.get_region_latest_videos(matched_rid, count=5)
            if not vlist:
                vlist = self.pipeline.bili_client.search_videos_by_keyword(query, count=5, order="totalrank")

            target_v = None
            from src.filter import check_video_relevance
            for v in vlist:
                ok, _, _ = check_video_relevance(
                    v,
                    target_keyword=query,
                    min_play=100,
                    min_duration_sec=15,
                    max_duration_sec=3600,
                    max_days_old=30,
                    min_relevance_score=0.60,
                    short_video_play_threshold=500
                )
                if ok:
                    target_v = v
                    break

            if not target_v and vlist:
                target_v = vlist[0]

            if not target_v:
                return f"⚠️ 未检索到关于【{query}】的高质量相关视频，请更换关键词重试。"

            target_bvid = target_v["bvid"]
            reqs = prof.get("requirements", "")

            self.pipeline.process_custom_video_for_user(
                bvid=target_bvid,
                receiver_uid=talker_uid,
                custom_requirements=reqs,
                source_label=f"即时指令_{query}",
                detailed=False
            )
            return None

        return None

    def _extract_bvid_and_prompt(self, raw_content: str, msg_type: int) -> tuple[Optional[str], str, str]:
        bvid = None
        user_prompt = ""

        try:
            data = json.loads(raw_content)
            if isinstance(data, dict):
                if "bvid" in data:
                    bvid = data["bvid"]
                elif "url" in data:
                    url = data["url"]
                    if "video/BV" in url:
                        bvid = "BV" + url.split("video/BV")[1].split("?")[0].split("/")[0]
                if "content" in data:
                    text = data["content"]
                else:
                    text = str(data)
            else:
                text = str(data)
        except Exception:
            text = raw_content

        if not bvid:
            bv_match = re.search(r'(BV[a-zA-Z0-9]{10})', text, re.IGNORECASE)
            if bv_match:
                bvid = bv_match.group(1)
            elif "b23.tv/" in text:
                b23_match = re.search(r'https?://b23\.tv/[a-zA-Z0-9]+', text)
                if b23_match:
                    from main import extract_bvid
                    bvid = extract_bvid(b23_match.group(0))

        clean_text = text
        if bvid:
            clean_text = re.sub(r'https?://\S+', '', clean_text)
            clean_text = re.sub(r'BV[a-zA-Z0-9]{10}', '', clean_text, flags=re.IGNORECASE)
            clean_text = clean_text.replace("总结", "").replace("分析", "").strip()
            user_prompt = clean_text

        return bvid, user_prompt, text

    def check_private_messages(self):
        targets = self.config.get("notification", {}).get("receiver_uids", [])
        for talker_uid in targets:
            msgs = self._fetch_messages(talker_uid, size=50)
            msgs_sorted = sorted(msgs, key=lambda x: x.get("timestamp", 0))

            for m in msgs_sorted:
                m_key = m.get("msg_key")
                if not m_key or m_key in self.processed_msg_keys:
                    continue
                if len(self.processed_msg_keys) > 2000:
                    self.processed_msg_keys = set(list(self.processed_msg_keys)[-1000:])
                self.processed_msg_keys.add(m_key)

                sender_uid = m.get("sender_uid")
                if sender_uid != talker_uid:
                    continue

                msg_type = m.get("msg_type")
                raw_content = m.get("content", "")

                bvid, extra_prompt, plain_text = self._extract_bvid_and_prompt(raw_content, msg_type)

                # A. 优先处理控制指令
                cmd_reply = self._handle_direct_command(talker_uid, plain_text)
                if cmd_reply:
                    print(f"\n[⚡ 收到配置指令] 来自 UID {talker_uid}: {plain_text}")
                    try:
                        self.pipeline.notifier.send_raw_text(talker_uid, cmd_reply)
                        print(f"[✓] 已成功回复配置结果至 UID {talker_uid}")
                    except Exception as e:
                        print(f"[!] 回复配置结果失败: {e}")
                    continue

                # B. 处理主动分享视频 -> 触发全量精细深度结构化报告 (detailed=True)
                if bvid:
                    profile = self.profiles.get(str(talker_uid), {})
                    account_name = profile.get("name", f"用户_{talker_uid}")
                    base_req = profile.get("requirements", "")
                    merged_req = f"{base_req}。附加重点关注: {extra_prompt}" if extra_prompt else base_req

                    print(f"\n[🔔 收到私信视频任务] 来自: {account_name} ({talker_uid})")
                    print(f"    - 目标视频: {bvid}")
                    if extra_prompt:
                        print(f"    - 附加指令: {extra_prompt}")

                    try:
                        self.pipeline.notifier.send_raw_text(
                            talker_uid,
                            f"🤖【BiliSummary】已收到视频《{bvid}》！\n正在调度算力进行高精度提炼，请稍候..."
                        )
                    except Exception as e:
                        print(f"[!] 回复确认私信失败: {e}")

                    self.pipeline.process_custom_video_for_user(
                        bvid=bvid,
                        receiver_uid=talker_uid,
                        custom_requirements=merged_req,
                        source_label=f"私信精读_{account_name}",
                        detailed=True
                    )

    def check_at_comments(self):
        if not self.listener_cfg.get("listen_at_comments", True):
            return

        ats = self._fetch_at_messages()
        for at in ats:
            at_id = at.get("id")
            if not at_id or at_id in self.processed_at_ids:
                continue
            self.processed_at_ids.add(at_id)

            item = at.get("item", {})
            user_info = at.get("user", {})
            from_uid = user_info.get("mid")
            nickname = user_info.get("nickname", "")
            source_content = item.get("source_content", "")
            target_id = item.get("target_id")
            uri = item.get("uri", "")

            bvid = None
            if "video/BV" in uri:
                bvid = "BV" + uri.split("video/BV")[1].split("?")[0].split("/")[0]
            elif target_id:
                try:
                    info = self.pipeline.bili_client.get_video_info(f"av{target_id}")
                    bvid = info.get("bvid")
                except Exception:
                    pass

            if bvid and from_uid:
                # 用户门控鉴权 (Access Control)
                whitelist = self.config.get("notification", {}).get("receiver_uids", [])
                if from_uid not in whitelist:
                    print(f"[🛡️ 门控拦截] 非白名单用户 @ 机器人: {nickname} (UID: {from_uid})，已自动忽略防止算力消耗")
                    continue

                print(f"[💬 收到评论区@任务] 白名单用户: {nickname} ({from_uid}) 在评论区 @ 了机器人")
                profile = self.profiles.get(str(from_uid), {})
                base_req = profile.get("requirements", "")
                merged_req = f"{base_req}。评论区关注重点: {source_content}"

                self.pipeline.process_custom_video_for_user(
                    bvid=bvid,
                    receiver_uid=from_uid,
                    custom_requirements=merged_req,
                    source_label=f"评论区@{nickname}",
                    detailed=True
                )

    def run_polling_loop(self):
        print(f"[*] 启动云端私信指令、视频分享与评论区@实时监听 (轮询周期: {self.poll_interval}s)...")
        loop_cnt = 0
        while True:
            try:
                self.check_private_messages()
                if self.listener_cfg.get("listen_at_comments", True):
                    self.check_at_comments()
            except Exception as e:
                print(f"[!] 监听轮询循环异常: {e}")

            loop_cnt += 1
            if loop_cnt % 10 == 0:
                import gc
                gc.collect()
                try:
                    import ctypes
                    ctypes.CDLL("libc.so.6").malloc_trim(0)
                except Exception:
                    pass

            time.sleep(self.poll_interval)
