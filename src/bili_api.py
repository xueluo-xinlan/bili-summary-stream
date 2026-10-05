import time
import hashlib
import re
import urllib.parse
from functools import reduce
from typing import Dict, Any, List, Optional
import requests

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52
]

def get_mixin_key(orig: str) -> str:
    return reduce(lambda s, i: s + orig[i], MIXIN_KEY_ENC_TAB, '')[:32]

def enc_wbi(params: Dict[str, Any], img_key: str, sub_key: str) -> Dict[str, Any]:
    mixin_key = get_mixin_key(img_key + sub_key)
    curr_time = round(time.time())
    params['wts'] = curr_time
    params = dict(sorted(params.items()))
    filtered = {}
    for k, v in params.items():
        v_str = ''.join(c for c in str(v) if c not in "!'()*")
        filtered[k] = v_str
    query = urllib.parse.urlencode(filtered)
    wbi_sign = hashlib.md5((query + mixin_key).encode('utf-8')).hexdigest()
    filtered['w_rid'] = wbi_sign
    return filtered

class BiliClient:
    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com",
        "Origin": "https://www.bilibili.com"
    }

    PARTITION_MAP = {
        "科技": 188, "科技区": 188,
        "计算机": 95, "计算机技术": 95,
        "软件": 230, "软件应用": 230,
        "极客": 231, "极客工程": 231,
        "数码": 96, "数码区": 96,
        "手机": 97, "手机平板": 97,
        "电脑": 98, "电脑装机": 98,
        "摄影": 99, "摄影摄像": 99,
        "知识": 36, "知识区": 36,
        "科普": 201, "科学科普": 201,
        "社科": 124, "法律心理": 124,
        "游戏": 4, "游戏区": 4,
        "动画": 1, "动画区": 1,
        "MMD": 24, "MMD·3D": 24, "3D动画": 24,
        "短片": 47, "动画综合": 27,
        "汽车": 223, "汽车区": 223,
        "财经": 207, "商业财经": 207
    }

    def __init__(self, sessdata: str = "", bili_jct: str = "", dedeuserid: str = ""):
        self.sessdata = sessdata
        self.bili_jct = bili_jct
        self.dedeuserid = dedeuserid
        self.session = requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)
        if self.sessdata:
            self.session.cookies.set("SESSDATA", self.sessdata, domain=".bilibili.com")
        if self.bili_jct:
            self.session.cookies.set("bili_jct", self.bili_jct, domain=".bilibili.com")
        if self.dedeuserid:
            self.session.cookies.set("DedeUserID", str(self.dedeuserid), domain=".bilibili.com")
        # 添加标准 buvid 防止部分防爬 412
        self.session.cookies.set("buvid3", "25B81057-D881-A6B5-B8AE-4DAF0F29DF9612345infoc", domain=".bilibili.com")

        self._img_key: Optional[str] = None
        self._sub_key: Optional[str] = None
        self._wbi_updated_at: float = 0

    def refresh_wbi_keys(self) -> None:
        now = time.time()
        if self._img_key and self._sub_key and (now - self._wbi_updated_at < 3600):
            return
        resp = self.session.get("https://api.bilibili.com/x/web-interface/nav", timeout=10)
        data = resp.json()
        if data.get("code") == 0:
            wbi_img = data["data"].get("wbi_img", {})
            img_url = wbi_img.get("img_url", "")
            sub_url = wbi_img.get("sub_url", "")
            self._img_key = img_url.rsplit('/', 1)[-1].split('.')[0]
            self._sub_key = sub_url.rsplit('/', 1)[-1].split('.')[0]
            self._wbi_updated_at = now
        else:
            self._img_key = "653657f524a547ac981deb0e8619b08d"
            self._sub_key = "245ae7015f124694939b4b1a4a4f87cf"

    def sign_wbi_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self.refresh_wbi_keys()
        return enc_wbi(params, self._img_key, self._sub_key)

    def get_video_info(self, bvid: str) -> Dict[str, Any]:
        url = "https://api.bilibili.com/x/web-interface/view"
        resp = self.session.get(url, params={"bvid": bvid}, timeout=10)
        res = resp.json()
        if res.get("code") != 0:
            raise ValueError(f"获取视频 {bvid} 信息失败: {res.get('message')}")
        data = res["data"]
        return {
            "bvid": data["bvid"],
            "aid": data["aid"],
            "title": data["title"],
            "desc": data["desc"],
            "duration": data["duration"],
            "pic": data["pic"],
            "owner_name": data["owner"]["name"],
            "owner_mid": data["owner"]["mid"],
            "cid": data["cid"],
            "pubdate": data["pubdate"],
            "pages": data.get("pages", []),
            # 分区字段用于「视频类型」判定（音乐类视频的歌词字幕是合法内容，
            # 而普通讲解视频出现歌词即意味着串台）
            "tname": data.get("tname", ""),
            "tid": data.get("tid", 0),
        }

    # 音乐/舞蹈/动画类分区号——这些分区的视频本就以歌、舞、画面为主体
    _MUSIC_TIDS = {
        3,      # 音乐
        129,    # 舞蹈
        130,    # 舞蹈→宅舞
        21,     # 日常→生活（含部分 MMD）
        27,     # 动画→综合
        24,     # MAD·AMV
        25,     # MMD·3D
        47,     # 短片·手书·配音
        85,     # 短片
        138,    # 搞笑
        156,    # 动画→手办·模玩
    }
    _MUSIC_TNAME_KEYS = ("音乐", "舞蹈", "MMD", "MAD", "AMV", "音MAD", "宅舞",
                         "短片", "手书", "翻唱", "演奏", "VOCALOID", "MV")

    def is_music_like(self, video_info: Dict[str, Any]) -> bool:
        """判断视频是否属于「以音乐/画面为主体」的类型。

        判据：B站分区号/分区名，辅以标题关键词。这类视频的字幕本就会是歌词，
        不能因「内容不是语音」而判为串台。
        """
        if not video_info:
            return False
        try:
            if int(video_info.get("tid") or 0) in self._MUSIC_TIDS:
                return True
        except (TypeError, ValueError):
            pass
        tname = str(video_info.get("tname") or "")
        title = str(video_info.get("title") or "")
        if any(k in tname for k in self._MUSIC_TNAME_KEYS):
            return True
        return any(k in title.upper() for k in
                   ("MMD", "MV", "MAD", "AMV", "纯音乐", "伴奏", "翻唱", "舞", "曲",
                    "歌", "VOCALOID", "BGM", "音乐"))

    @staticmethod
    def looks_like_lyrics(text: str) -> bool:
        """歌词形态启发式：♪ 记号密度高，且句子短促重复。"""
        if not text:
            return False
        head = text[:2000]
        marks = head.count("♪") + head.count("♫")
        if marks >= 3:
            return True
        # 无 ♪ 记号时看句长：歌词普遍短句、少连接词
        segs = [s for s in re.split(r"[。！？\n]", head) if s.strip()]
        if len(segs) >= 8:
            avg = sum(len(s) for s in segs) / len(segs)
            if avg <= 14:
                return True
        return False

    def get_audio_stream_url(self, bvid: str, cid: int) -> str:
        params = {"bvid": bvid, "cid": cid, "fnval": 16}
        signed_params = self.sign_wbi_params(params)
        url = "https://api.bilibili.com/x/player/wbi/playurl"
        resp = self.session.get(url, params=signed_params, timeout=10)
        res = resp.json()
        if res.get("code") != 0:
            raise ValueError(f"获取播放地址失败 ({bvid}): {res.get('message')}")
        dash = res.get("data", {}).get("dash", {})
        audios = dash.get("audio", [])
        if not audios:
            raise ValueError(f"视频 {bvid} 未找到有效音频 DASH 流")
        audios_sorted = sorted(audios, key=lambda x: x.get("bandwidth", 0))
        target_audio = audios_sorted[0]
        return target_audio.get("baseUrl") or target_audio.get("base_url")

    def get_video_stream_url(self, bvid: str, cid: int) -> str:
        """取**码率最低**的视频 DASH 流——仅用于抽帧做视觉理解，无需高清。"""
        params = {"bvid": bvid, "cid": cid, "fnval": 16}
        signed_params = self.sign_wbi_params(params)
        url = "https://api.bilibili.com/x/player/wbi/playurl"
        res = self.session.get(url, params=signed_params, timeout=10).json()
        if res.get("code") != 0:
            raise ValueError(f"获取播放地址失败 ({bvid}): {res.get('message')}")
        videos = (res.get("data", {}).get("dash", {}) or {}).get("video", [])
        if not videos:
            raise ValueError(f"视频 {bvid} 未找到有效视频 DASH 流")
        # 优先选编码兼容性最佳的 avc，再取最低码率
        avc = [v for v in videos if str(v.get("codecs", "")).startswith("avc")] or videos
        target = sorted(avc, key=lambda x: x.get("bandwidth", 0))[0]
        return target.get("baseUrl") or target.get("base_url")

    @staticmethod
    def _subtitle_rank(entry: Dict[str, Any]) -> int:
        """字幕条目优先级：人工中文 > AI中文 > 繁体中文 > 其它中文 > 其它语种。"""
        if not entry.get("subtitle_url"):
            return 99
        lan = str(entry.get("lan", "")).lower()
        doc = str(entry.get("lan_doc", ""))
        if lan.startswith("ai-"):
            return 1 if lan == "ai-zh" else 4
        if lan.startswith("zh"):
            return 2 if ("hant" in lan or "繁體" in doc or "繁体" in doc) else 0
        if "中文" in doc:
            return 3
        return 5

    @staticmethod
    def _build_subtitle_result(body: List[Dict[str, Any]]) -> Dict[str, Any]:
        timeline_lines, full_text_lines = [], []
        for item in body:
            start_sec = int(item.get("from", 0))
            end_sec = int(item.get("to", 0))
            start_m, start_s = divmod(start_sec, 60)
            end_m, end_s = divmod(end_sec, 60)
            time_str = f"[{start_m:02d}:{start_s:02d} -> {end_m:02d}:{end_s:02d}]"
            content = str(item.get("content", "")).strip()
            if content:
                timeline_lines.append(f"{time_str} {content}")
                full_text_lines.append(content)
        return {
            "source": "official_subtitle",
            "duration": body[-1].get("to", 0) if body else 0,
            "full_text": "".join(full_text_lines),
            "timeline_text": "\n".join(timeline_lines),
        }

    def get_official_subtitles(self, bvid: str, cid: int,
                               expected_duration: float = None) -> Optional[Dict[str, Any]]:
        """
        拉取 B 站官方 / AI 字幕（带重试、多条目回退与**串台校验**）。

        ⚠️ 接口实测存在两类缺陷：
        1. **间歇性失败**：同一视频连查，有时列表为空，有时列表正常但正文取不到。
        2. **返回其它视频的字幕**：同一 (bvid,cid) 连查 5 次，4 次拿到的是完全无关的
           内容（其它视频的评测、股票、英文文本…），且末时刻常远超本视频时长。

        第 2 类若不加拦截会产出"对错误视频的自信总结"，比空壳更危险。故此处用
        **时长闸门**先挡一道（调用方还可用 verify_relevance 做内容相关性复核）。

        返回: {'timeline_text', 'full_text', 'duration', 'source'}
        """
        LIST_ATTEMPTS, BODY_ATTEMPTS, BACKOFF = 3, 3, 1.5

        list_url = f"https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={cid}"
        subs: List[Dict[str, Any]] = []
        for attempt in range(1, LIST_ATTEMPTS + 1):
            try:
                res = self.session.get(list_url, timeout=10).json()
                if res.get("code") == 0:
                    subs = (res.get("data", {}).get("subtitle", {}) or {}).get("subtitles") or []
            except Exception as e:
                if attempt == LIST_ATTEMPTS:
                    print(f"[!] 字幕列表拉取异常 ({bvid}): {e}")
            if subs:
                break
            if attempt < LIST_ATTEMPTS:
                time.sleep(BACKOFF ** attempt)

        if not subs:
            return None

        for entry in sorted(subs, key=self._subtitle_rank):
            sub_url = entry.get("subtitle_url") or ""
            if not sub_url:
                continue
            if sub_url.startswith("//"):
                sub_url = "https:" + sub_url
            for attempt in range(1, BODY_ATTEMPTS + 1):
                try:
                    # 走 self.session：保留 Referer/Cookie，比裸 requests 更稳
                    body = self.session.get(sub_url, timeout=10).json().get("body") or []
                    if body:
                        result = self._build_subtitle_result(body)
                        if not result["full_text"]:
                            continue
                        # —— 时长闸门：错字幕的末时刻常远超或远低于本视频时长 ——
                        if expected_duration and expected_duration > 0:
                            end = float(result.get("duration") or 0)
                            if end > 0:
                                drift = abs(end - expected_duration) / expected_duration
                                if drift > 0.15:
                                    print(f"[!] 字幕串台拦截：字幕末 {end:.0f}s vs "
                                          f"视频 {expected_duration:.0f}s（偏差 {drift*100:.0f}%），拒收")
                                    result = None
                        if result:
                            return result
                except Exception:
                    pass
                if attempt < BODY_ATTEMPTS:
                    time.sleep(BACKOFF ** attempt)

        return None

    def search_videos_by_keyword(
        self,
        keyword: str,
        count: int = 5,
        order: str = "totalrank",
        tid: int = 0
    ) -> List[Dict[str, Any]]:
        url = "https://api.bilibili.com/x/web-interface/wbi/search/type"
        params = {
            "keyword": keyword,
            "search_type": "video",
            "order": order,
            "page": 1,
            "pagesize": max(count * 4, 15)  # 扩大拉取池至 15~20 条，便于算法做深度挑选
        }
        if tid:
            params["tids"] = tid

        signed = self.sign_wbi_params(params)
        resp = self.session.get(url, params=signed, timeout=10)
        res = resp.json()
        if res.get("code") != 0:
            return []
        vlist = res.get("data", {}).get("result", [])
        result = []
        for v in vlist:
            clean_title = v.get("title", "").replace('<em class="keyword">', '').replace('</em>', '')
            raw_play = v.get("play", 0)
            try:
                play_num = int(raw_play)
            except Exception:
                play_num = 0

            dur_str = str(v.get("duration", "0"))
            dur_sec = 0
            if ":" in dur_str:
                parts = dur_str.split(":")
                if len(parts) == 2:
                    dur_sec = int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 3:
                    dur_sec = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            elif dur_str.isdigit():
                dur_sec = int(dur_str)

            result.append({
                "bvid": v.get("bvid"),
                "title": clean_title,
                "desc": v.get("description", ""),
                "pic": "https:" + v.get("pic", "") if v.get("pic", "").startswith("//") else v.get("pic", ""),
                "author": v.get("author", ""),
                "pubdate": v.get("pubdate", 0),
                "duration": dur_sec,
                "play": play_num
            })
        return result

    def get_region_latest_videos(self, rid: int, count: int = 5) -> List[Dict[str, Any]]:
        url = "https://api.bilibili.com/x/web-interface/dynamic/region"
        params = {"rid": rid, "ps": count, "pn": 1}
        resp = self.session.get(url, params=params, timeout=10)
        res = resp.json()
        if res.get("code") != 0:
            return []
        archives = res.get("data", {}).get("archives", [])
        result = []
        for v in archives[:count]:
            result.append({
                "bvid": v.get("bvid"),
                "title": v.get("title", ""),
                "desc": v.get("desc", ""),
                "pic": v.get("pic", ""),
                "author": v.get("owner", {}).get("name", ""),
                "pubdate": v.get("pubdate", 0),
                "duration": v.get("duration", 0),
                "play": v.get("stat", {}).get("view", 0)
            })
        return result

    def get_up_latest_videos(self, mid: int, count: int = 5) -> List[Dict[str, Any]]:
        url = "https://api.bilibili.com/x/space/wbi/arc/search"
        params = {"mid": mid, "ps": count, "pn": 1, "order": "pubdate"}
        signed = self.sign_wbi_params(params)
        resp = self.session.get(url, params=signed, timeout=10)
        res = resp.json()
        if res.get("code") != 0:
            return []
        vlist = res.get("data", {}).get("list", {}).get("vlist", [])
        return [{
            "bvid": v["bvid"],
            "title": v["title"],
            "desc": v.get("description", ""),
            "pic": v.get("pic", ""),
            "author": v.get("author", ""),
            "mid": mid,
            "created": v.get("created", 0)
        } for v in vlist[:count]]
