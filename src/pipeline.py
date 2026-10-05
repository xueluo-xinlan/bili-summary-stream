import time
from typing import Dict, Any

from src.bili_api import BiliClient
from src.transcriber_client import HybridTranscriberClient, source_label as content_source_label
from src.summarizer import LLMSummarizer
from src.notifier import BiliNotifier
from src.storage import Storage
from src.filter import check_video_relevance

# 一份真正的速读摘要至少该有一句话。低于此长度视为"空摘要"，
# 一律拒绝推送——宁可不发，也不要发出只有标题头的空消息。
MIN_SUMMARY_CHARS = 40


class SummaryPipeline:
    def __init__(self, config: Dict[str, Any]):
        self.config = config

        bili_cfg = config.get("bilibili", {})
        self.bili_client = BiliClient(
            sessdata=bili_cfg.get("sessdata", ""),
            bili_jct=bili_cfg.get("bili_jct", ""),
            dedeuserid=str(bili_cfg.get("sender_uid", ""))
        )

        llm_cfg = config.get("llm", {})

        # 云端多模态内容提取（音频转写 + 视觉描述）——让 PC 关机时也能拿到真实内容
        cloud_cfg = config.get("cloud_mm", {}) or {}
        self.cloud_mm = None
        if cloud_cfg.get("enabled", True):
            from src.cloud_mm import CloudMMExtractor
            self.cloud_mm = CloudMMExtractor(
                base_url=llm_cfg.get("base_url", "http://127.0.0.1:8317/v1"),
                api_key=llm_cfg.get("api_key", "EMPTY"),
                model=cloud_cfg.get("model") or llm_cfg.get("model", "gemini-3.8-flash-high"),
                work_dir=cloud_cfg.get("work_dir", "cache/cloud_mm"),
                vision_frames=cloud_cfg.get("vision_frames", 6),
                audio_bitrate=cloud_cfg.get("audio_bitrate", "32k"),
                timeout=cloud_cfg.get("timeout", 300),
            )

        hybrid_cfg = config.get("hybrid", {})
        self.transcriber = HybridTranscriberClient(
            bili_client=self.bili_client,
            laptop_worker_url=hybrid_cfg.get("laptop_worker_url", "http://192.168.1.50:18088"),
            probe_timeout=hybrid_cfg.get("probe_timeout", 0.8),
            cloud_mm=self.cloud_mm,
            enable_cloud=cloud_cfg.get("enabled", True),
            # 字幕通道：实测取回的正文约 75% 属**其它视频**，已按 config 显式关闭。
            # 保留开关是为了需要时能临时打开（打开则必须过相关性闸门）。
            enable_subtitles=cloud_cfg.get("subtitle_channel", True),
        )

        self.summarizer = LLMSummarizer(
            base_url=llm_cfg.get("base_url", "http://127.0.0.1:8317/v1"),
            api_key=llm_cfg.get("api_key", "EMPTY"),
            model=llm_cfg.get("model", "gemini-3.8-flash-high"),
            temperature=llm_cfg.get("temperature", 0.3)
        )

        notify_cfg = config.get("notification", {})
        receiver_targets = notify_cfg.get("receiver_uids") or notify_cfg.get("receiver_uid") or []
        self.notifier = BiliNotifier(
            sessdata=bili_cfg.get("sessdata", ""),
            bili_jct=bili_cfg.get("bili_jct", ""),
            sender_uid=bili_cfg.get("sender_uid", 0),
            receiver_uids=receiver_targets,
            max_chunk_size=notify_cfg.get("max_chunk_size", 700),
            send_interval=notify_cfg.get("send_interval", 1.5)
        )

        self.storage = Storage(
            db_path=config.get("paths", {}).get("history_db", "data/history.db")
        )

    def process_bvid(self, bvid: str, source_type: str = "manual", force: bool = False) -> bool:
        if not force and self.storage.is_processed(bvid):
            print(f"[i] 视频 {bvid} 已在历史库中，跳过处理")
            return True

        print("\n==========================================")
        print(f"[*] 开始全量处理视频: {bvid} (来源: {source_type})")
        print("==========================================")

        try:
            info = self.bili_client.get_video_info(bvid)
            print(f"[+] 视频标题: 《{info['title']}》")
            print(f"[+] UP 主: {info['owner_name']} | 时长: {info['duration']} 秒")
        except Exception as e:
            print(f"[!] 抓取视频详情失败 ({bvid}): {e}")
            return False

        try:
            audio_url = self.bili_client.get_audio_stream_url(bvid, info['cid'])
        except Exception:
            audio_url = ""

        try:
            transcription = self.transcriber.transcribe(
                bvid=bvid,
                cid=info['cid'],
                audio_url=audio_url,
                video_info=info
            )
        except Exception as e:
            print(f"[!] 内容转写提取失败: {e}")
            return False

        # 铁律：拿不到真实文本就排队补推，绝不用「标题+简介」编造"空壳摘要"。
        # 空壳最伤人的地方不是空洞，而是它看起来和真货一模一样。
        if not transcription or not transcription.get("full_text"):
            trace = " → ".join(getattr(self.transcriber, "trace", []) or [])
            print(f"[i] 未取得真实内容，记入待补推: {bvid}（{trace}）")
            self.storage.mark_pending(
                bvid=bvid, title=info["title"], owner_name=info["owner_name"],
                source_type=source_type, note=trace,
            )
            self._notify_pending(info)
            return False

        try:
            summary = self.summarizer.summarize(info, transcription, detailed=True)
            print(f"[+] 总结生成成功! 总结字数: {len(summary)}")
        except Exception as e:
            print(f"[!] LLM 总结失败: {e}")
            return False

        # 最后防线（三态）：放行 / 取源故障拦下 / 可信来源的文不对题走中性披露
        #
        # ⚠️ 顺序铁律：这一步**必须早于**空摘要闸门。
        # 摘要器判定「文不对题」时返回的是 `__CONTENT_MISMATCH__`（20 字），
        # 若空摘要闸门先跑，会把它当"空摘要"直接拦死 → 能救回内容的
        # `_neutral_rewrite()` 永远轮不到执行 → **那些标题与内容确实不符的视频
        # 会永远卡在待补推队列里，每次巡检重试、每次被拦、永远推不出去**。
        # 实测：BV1A8YX6uE1j 常规摘要 20 字被拦，中性披露重写实为 259 字（本该发出去）。
        guard = self._guard_summary(summary, info, bvid, source_type,
                                    transcription.get("source", ""))
        if guard is None:
            summary = self._neutral_rewrite(info, transcription, detailed=True)
            if summary is None:
                self._notify_pending(info)
                return False
        elif not guard:
            self._notify_pending(info)
            return False

        # 空摘要闸门：拒绝推送光杆标题（检查的是上面**重写后**的摘要）
        if not self._summary_usable(summary, bvid):
            self._notify_pending(info)
            return False

        try:
            self.notifier.send_summary_card(
                video_info=info,
                summary_markdown=summary,
                fallback_dir=self.config.get("paths", {}).get("summary_dir", "summaries"),
                source_note=content_source_label(transcription.get("source", "")),
            )
        except Exception as e:
            print(f"[!] 私信推送发生异常: {e}")

        self.storage.mark_processed(
            bvid=bvid,
            title=info["title"],
            owner_name=info["owner_name"],
            source_type=source_type,
            status="success",
            note=content_source_label(transcription.get("source", "")),
        )
        print(f"[✓] 视频 {bvid} 全流程顺利完成！\n")
        return True

    def _summary_usable(self, summary: str, bvid: str = "") -> bool:
        """空摘要闸门：拒绝推送「光杆标题」。

        历史事故（09-15 定位）：旧的「元数据兜底」路径把「标题+简介」喂给摘要器，
        而这几支视频简介为空，模型只剩标题可依 → 返回空字符串 → 代码**没有任何检查**，
        直接把只有标题头的空消息发了出去。用户收到 6 条"有头无身"的噪音。
        根因已随元数据兜底路径一并移除，但**这个闸门必须独立存在**：
        任何原因（模型截断、内容过滤、max_tokens 耗尽）导致的空摘要都不该被发出去。
        """
        text = (summary or "").strip()
        if len(text) >= MIN_SUMMARY_CHARS:
            return True
        print(f"[!] 空摘要闸门：{bvid} 摘要仅 {len(text)} 字（阈值 {MIN_SUMMARY_CHARS}），"
              f"拒绝推送光杆标题")
        return False

    def _guard_summary(self, summary: str, info: Dict[str, Any], bvid: str,
                       source_type: str = "manual", source: str = ""):
        """最后一道防线。返回三态：

        ``True``  正常，可推送
        ``False`` 已拦下（非可信来源的内容不符＝我们的取源故障），已记入待补推
        ``None``  可信来源但视频确实文不对题 → 需改用「中性披露」重写后再推

        历史事故（09-15 复核确认至少 13 次）：字幕通道串台取到了别人视频的字幕，
        摘要器察觉到标题与内容不符，却把它写成「严重标题党」「挂羊头卖狗肉」
        「虚假引流欺诈」——把自己的数据错误栽赃给无辜的 UP 主，甚至劝用户"划走"。
        这比推空壳更恶劣，必须拦死。

        但**不能一刀切**：若内容出自本机GPU/云端ASR/云端视觉，那是我们按
        bvid+cid 亲自取回的音视频，内容必然属于该视频——此时不符是**视频自身的
        属性**，拦下反而等于永远不推。正确处理是中性披露后照实交付。
        """
        from src.summarizer import (MISMATCH_MARKER, TRUSTED_SOURCES,
                                    looks_like_mismatch_rationalisation)

        if not looks_like_mismatch_rationalisation(summary):
            return True

        why = MISMATCH_MARKER if MISMATCH_MARKER in summary else "甩锅措辞（标题党/挂羊头卖狗肉等）"

        if source in TRUSTED_SOURCES:
            print(f"[i] 内容一致性：{bvid} 标题与实际内容不符，但来源={source} 属可信通道")
            print("    （音视频是我们按 bvid+cid 亲自取回的，内容必然属于该视频）")
            print("    → 判定为**视频自身属性**，改用中性披露重写，不做指控、不下判决。\n")
            return None

        print(f"[!!!] 内容一致性告警：{bvid}")
        print(f"      标题: {str(info.get('title', ''))[:50]}")
        print(f"      来源: {source or '未知'}（非可信通道）")
        print("      摘要器报告「内容与标题不符」→ 判定为上游取源故障，**不予推送**。")
        print(f"      判定依据: {why}")
        print("      处置: 记入待补推队列，等待用更可靠的内容源重试。\n")

        self.storage.mark_pending(
            bvid=bvid,
            title=info.get("title", ""),
            owner_name=info.get("owner_name", ""),
            source_type=source_type,
            note=f"内容与标题不符（疑似上游取源错误），已拦截未推送：{why}",
        )
        return False

    def _neutral_rewrite(self, info: Dict[str, Any], transcription: Dict[str, Any],
                         custom_requirements: str = "", detailed: bool = False):
        """可信来源 + 视频确实文不对题 → 改写为「中性披露」稿。

        只陈述观察到的事实（实际内容与标题主题不同）并照实提炼**实际内容**；
        不得指责任何人、不得用「标题党/挂羊头卖狗肉/欺诈」等词、不得劝人避坑。
        返回 None 表示放弃推送（重写仍不达标）。
        """
        from src.summarizer import looks_like_mismatch_rationalisation

        try:
            s = self.summarizer.summarize(
                video_info=info,
                transcription=transcription,
                custom_requirement=custom_requirements,
                detailed=detailed,
                mismatch_disclosure=True,
            )
        except Exception as e:
            print(f"[!] 中性披露重写失败: {e}")
            return None

        if not s or looks_like_mismatch_rationalisation(s):
            print("[!] 中性披露重写后仍带指控性措辞，放弃推送（宁可不发，也不栽赃作者）")
            return None

        print("[i] 已按「中性披露」重写：只陈述实际内容，不评判作者。")
        return s

    def _notify_pending(self, info: Dict[str, Any]) -> None:
        """待补推时的**诚实通知**：不遗漏，也不编造。

        宁可用户收到一条"暂无可用的真实内容"，也不要他既收不到、又不知道。
        可用 ``pending_notice: false`` 关闭，改为完全静默排队。
        """
        if not self.config.get("pending_notice", True):
            print("[i] pending_notice 已关闭，静默排队")
            return
        msg = (
            f"⏳【待补推】\n"
            f"《{info.get('title')}》\n"
            f"UP主: {info.get('owner_name')}\n"
            f"直达链接: https://b23.tv/{info.get('bvid')}\n"
            f"------------------------\n"
            f"本期暂未取得可用的真实内容（GPU 转写、B站字幕、云端转写与画面描述均未成功）。\n"
            f"已记入待补推队列，稍后会自动补推完整版——而不是先发一份编出来的摘要。"
        )
        try:
            for uid in self.notifier.receiver_uids:
                self.notifier.send_raw_text(uid, msg)
                time.sleep(1.0)
            print(f"[i] 已发送待补推通知至 {len(self.notifier.receiver_uids)} 个账号")
        except Exception as e:
            print(f"[!] 待补推通知发送失败: {e}")

    def retry_pending(self, limit: int = 3) -> int:
        """补推队列：优先用更高层级的内容来源重试此前未取得真实文本的视频。

        幂等：成功即写回 status=success，不会再被取出。
        """
        pending = self.storage.get_pending(limit=limit)
        if not pending:
            return 0
        print(f"\n[⟳ 补推队列] 待补推 {len(pending)} 条，本轮尝试 {min(limit, len(pending))} 条 ...")
        done = 0
        for row in pending:
            bvid = row.get("bvid", "")
            if not bvid:
                continue
            print(f"  → 补推尝试: {bvid} 《{(row.get('title') or '')[:24]}》")
            try:
                if self.process_bvid(bvid, source_type=row.get("source_type") or "pending_retry",
                                     force=True):
                    done += 1
            except Exception as e:
                print(f"  [!] 补推异常 {bvid}: {e}")
        print(f"[⟳ 补推队列] 本轮补齐 {done} / {len(pending)} 条")
        return done

    def process_custom_video_for_user(
        self,
        bvid: str,
        receiver_uid: int,
        custom_requirements: str = "",
        source_label: str = "custom",
        detailed: bool = False
    ) -> bool:
        dedup_key = f"{receiver_uid}_{bvid}"
        if self.storage.is_processed(dedup_key):
            print(f"[i] 视频 {bvid} 已为账号 {receiver_uid} 处理过，跳过")
            return True

        mode_text = "全量精细深度版" if detailed else "轻量速读版"
        print("\n==========================================")
        print(f"[*] 为账号 {receiver_uid} 生成总结 ({mode_text}): {bvid} ({source_label})")
        if custom_requirements:
            print(f"[*] 专属偏好要求: {custom_requirements[:60]}...")
        print("==========================================")

        try:
            info = self.bili_client.get_video_info(bvid)
        except Exception as e:
            print(f"[!] 抓取视频详情失败 ({bvid}): {e}")
            return False

        try:
            audio_url = self.bili_client.get_audio_stream_url(bvid, info['cid'])
        except Exception:
            audio_url = ""

        try:
            transcription = self.transcriber.transcribe(
                bvid=bvid,
                cid=info['cid'],
                audio_url=audio_url,
                video_info=info
            )
        except Exception as e:
            print(f"[!] 内容转写提取失败: {e}")
            return False

        # 铁律同样适用于手动请求：没有真实文本就不编，改为排队补推
        if not transcription or not transcription.get("full_text"):
            trace = " → ".join(getattr(self.transcriber, "trace", []) or [])
            print(f"[i] 未取得真实内容（{trace}），记入待补推: {bvid}")
            self.storage.mark_pending(
                bvid=dedup_key, title=info["title"], owner_name=info["owner_name"],
                source_type=source_label, note=trace,
            )
            self.notifier.send_raw_text(
                receiver_uid,
                f"⏳【待补推】\n《{info.get('title')}》\n"
                f"https://b23.tv/{bvid}\n------------------------\n"
                f"本期暂未取得可用的真实内容（{trace}），已记入待补推队列，稍后自动补推。",
            )
            return False

        try:
            summary = self.summarizer.summarize(
                video_info=info,
                transcription=transcription,
                custom_requirement=custom_requirements,
                detailed=detailed
            )
        except Exception as e:
            print(f"[!] LLM 总结失败: {e}")
            return False

        # 最后防线（三态）：放行 / 取源故障拦下 / 可信来源的文不对题走中性披露
        #
        # ⚠️ 与巡检路径同一条铁律：**必须先于空摘要闸门**。
        # 摘要器判定文不对题时返回 `__CONTENT_MISMATCH__`（20 字），
        # 空摘要闸门先跑就会拦死它，中性披露重写永远轮不到执行。
        guard = self._guard_summary(summary, info, bvid, source_label,
                                    transcription.get("source", ""))
        if guard is None:
            summary = self._neutral_rewrite(info, transcription,
                                            custom_requirements, detailed)
            if summary is None:
                self.notifier.send_raw_text(
                    receiver_uid,
                    f"⏳【暂缓推送】\n《{info.get('title')}》\n"
                    f"https://b23.tv/{bvid}\n------------------------\n"
                    f"本期内容与标题差异较大，为避免发出带偏见的内容，已暂缓并记入待补推队列。",
                )
                return False
        elif not guard:
            self.notifier.send_raw_text(
                receiver_uid,
                f"⏳【暂缓推送】\n《{info.get('title')}》\n"
                f"https://b23.tv/{bvid}\n------------------------\n"
                f"本期内容与标题明显不符（疑似取源错误），为避免把错误内容发给您，"
                f"已暂缓推送并记入待补推队列，稍后用更可靠的内容源重试。",
            )
            return False

        # 空摘要闸门：拒绝推送光杆标题（检查的是上面**重写后**的摘要）
        if not self._summary_usable(summary, bvid):
            self.notifier.send_raw_text(
                receiver_uid,
                f"⏳【暂缓推送】\n《{info.get('title')}》\n"
                f"https://b23.tv/{bvid}\n------------------------\n"
                f"本期未能生成有效总结（内容为空），为避免发出空消息，已暂缓并记入待补推队列。",
            )
            return False

        tag_title = "🎬【精细深度拆解】" if detailed else "⚡【精选动态速读】"
        src_note = content_source_label(transcription.get("source", ""))
        header = (f"{tag_title}\n《{info.get('title')}》\n"
                  f"UP主: {info.get('owner_name')} | 时长: {info.get('duration')}s\n"
                  f"直达链接: https://b23.tv/{bvid}\n"
                  f"内容来源: {src_note}\n"
                  f"------------------------\n")
        full_msg = header + summary
        chunks = self.notifier._split_message(full_msg)

        print(f"[*] 正在向 UID: {receiver_uid} 发送私信 (共 {len(chunks)} 条分段)...")
        for idx, chunk in enumerate(chunks, 1):
            try:
                self.notifier.send_raw_text(receiver_uid, chunk)
                print(f"  [✓] 分段 {idx}/{len(chunks)} 送达")
            except Exception as e:
                print(f"  [✗] 分段 {idx}/{len(chunks)} 发送失败: {e}")
            if idx < len(chunks):
                time.sleep(self.notifier.send_interval)

        self.storage.mark_processed(
            bvid=dedup_key,
            title=info["title"],
            owner_name=info["owner_name"],
            source_type=source_label,
            status="success",
            # 记下这份总结建立在哪种内容源上。此前这里漏传，导致历史库 38 条记录的
            # 「来源」为空——而"张冠李戴"审计恰恰最依赖这一列。
            note=content_source_label(transcription.get("source", "")),
        )
        print(f"[✓] 账号 {receiver_uid} 视频 {bvid} 总结推送完成！\n")
        return True

    def scan_and_process_profiles(self):
        profiles = self.config.get("profiles", {})
        if not profiles:
            return

        # 兜底清扫：进程被 kill / 崩溃时会漏掉按视频的临时文件清理，
        # 这里按时间再扫一遍，避免 cache/cloud_mm 无限膨胀（单个音频约 4.7MB）。
        try:
            if self.cloud_mm is not None:
                _n = self.cloud_mm.cleanup_stale(max_age_hours=24)
                if _n:
                    print(f"[i] 已清扫过期临时文件 {_n} 个")
        except Exception as _e:
            print(f"[i] 临时文件清扫跳过: {str(_e)[:60]}")

        for uid_str, prof in profiles.items():
            uid = int(uid_str)
            name = prof.get("name", f"用户_{uid}")
            keywords = prof.get("keywords", [])
            up_mids = prof.get("up_mids", [])
            reqs = prof.get("requirements", "")

            print(f"\n[>>> 正在巡检账号【{name}】(UID: {uid}) 专属监控源 <<<]")
            tasks = []

            for kw in keywords:
                try:
                    candidates = self.bili_client.search_videos_by_keyword(kw, count=5, order="totalrank")
                    max_push_per_kw = self.config.get("filter", {}).get("max_push_per_keyword", 1)

                    scored_candidates = []
                    for v in candidates:
                        bvid = v["bvid"]
                        dedup_key = f"{uid}_{bvid}"
                        if self.storage.is_processed(dedup_key):
                            continue

                        ok, reason, score = check_video_relevance(
                            video=v,
                            target_keyword=kw,
                            min_play=self.config.get("filter", {}).get("min_play", 500),
                            min_duration_sec=self.config.get("filter", {}).get("min_duration", 15),
                            max_duration_sec=self.config.get("filter", {}).get("max_duration", 2400),
                            max_days_old=self.config.get("filter", {}).get("max_days_old", 7),
                            min_relevance_score=self.config.get("filter", {}).get("min_relevance_score", 0.65),
                            short_video_play_threshold=self.config.get("filter", {}).get("short_video_play_threshold", 1500),
                            llm_verify=True,
                            llm_base_url=self.config.get("llm", {}).get("base_url", "http://127.0.0.1:8317/v1"),
                            llm_api_key=self.config.get("llm", {}).get("api_key", ""),
                            llm_model=self.config.get("llm", {}).get("model", "gemini-3.8-flash-high")
                        )
                        if ok:
                            scored_candidates.append((score, v["pubdate"], bvid))

                    scored_candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
                    for _, _, bvid in scored_candidates[:max_push_per_kw]:
                        tasks.append((bvid, f"精选_{kw}"))
                except Exception as e:
                    print(f"[!] 检索关键词 {kw} 异常: {e}")

            for mid in up_mids:
                try:
                    vlist = self.bili_client.get_up_latest_videos(mid, count=2)
                    for v in vlist:
                        tasks.append((v["bvid"], f"UP_{v.get('author', mid)}"))
                except Exception as e:
                    print(f"[!] 检索 UP主 {mid} 稿件异常: {e}")

            unprocessed = []
            seen = set()
            for bvid, src in tasks:
                dedup_key = f"{uid}_{bvid}"
                if bvid and bvid not in seen and not self.storage.is_processed(dedup_key):
                    seen.add(bvid)
                    unprocessed.append((bvid, src))

            print(f"[*] 账号【{name}】待处理新视频: {len(unprocessed)} 个")
            for bvid, src in unprocessed:
                self.process_custom_video_for_user(
                    bvid=bvid,
                    receiver_uid=uid,
                    custom_requirements=reqs,
                    source_label=src,
                    detailed=False
                )
                time.sleep(2)
