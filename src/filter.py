import time
import re
import json
from typing import Dict, Any

NEGATIVE_KEYWORDS = [
    "全748集", "全500集", "全100集", "全套教程", "零基础到项目实战",
    "少走99%的弯路", "存下吧", "很难找全", "允许白嫖", "直通offer",
    "上岸集训营", "就业课", "数据标注快速入门", "广告", "拼多多", "淘宝"
]

VISUAL_ART_KEYWORDS = [
    "mmd", "3d动画", "同人3d", "动画短片", "手书", "混剪", "mad", "amv", "舞蹈", "动作演示"
]

# 常见跨词重叠黑名单词对 (零内存损耗精准切断)
COLLISION_PROTECTIONS = {
    "色情": ["蓝色", "彩色", "特色", "变色", "成色", "情人节", "心情", "情绪", "风情", "真情", "盛色", "色调"],
    "本子": ["笔记本", "草稿本", "账本", "课本", "绘本", "书本", "作业本", "原本子", "根本子"],
    "历史": ["历史最高", "历史新高", "创造历史", "历史级", "历史最低", "破历史"],
    "色气": ["天色气", "暮色气", "成色气"],
    "坏女人": ["不仅是坏女人"]
}

def calculate_relevance_score(title: str, keyword: str) -> tuple[float, str]:
    t_clean = title.strip().lower()
    k_clean = keyword.strip().lower()

    if not k_clean:
        return 1.0, "空关键词"

    # 1. 跨词字符重叠碰撞保护 (0 内存秒级拦截)
    if k_clean in COLLISION_PROTECTIONS:
        forbidden_contexts = COLLISION_PROTECTIONS[k_clean]
        for fc in forbidden_contexts:
            if fc in t_clean and k_clean not in t_clean.replace(fc, ""):
                return 0.0, f"检测到跨词重叠碰撞（包含 '{fc}'，非独立语义）"

    # 2. 复合分词联合命中校验 (如 'AI大模型')
    tokens = [p.strip() for p in re.split(r'[\s_\-+]+', k_clean) if p.strip()]
    if not tokens:
        tokens = [k_clean]

    if "ai" in k_clean and "大模型" in k_clean:
        if "ai" in t_clean and "大模型" in t_clean:
            return 0.85, "核心子词(AI+大模型)全部命中"
        elif "大模型" in t_clean:
            return 0.70, "大模型核心词命中"
        elif "ai" in t_clean:
            return 0.20, "仅命中泛化字母AI"

    # 3. 完整命中判定
    if k_clean in t_clean:
        density = len(k_clean) / max(len(t_clean), 1)
        score = 0.8 + min(density * 0.5, 0.2)
        return score, "完整关键词完全命中"

    hits = sum(1 for tok in tokens if tok in t_clean)
    hit_ratio = hits / len(tokens)
    if hit_ratio >= 1.0:
        return 0.70, "复合子词全量命中"

    return 0.0, "未命中关键词核心语义"

def verify_semantic_relevance_with_llm(
    title: str,
    desc: str,
    keyword: str,
    base_url: str = "http://127.0.0.1:8317/v1",
    api_key: str = "EMPTY",
    model: str = "gemini-3.8-flash-high"
) -> tuple[bool, str]:
    """
    轻量零样本语义核验：通过本地或云端端点进行真伪判定，0 本地常驻内存开销
    """
    import requests
    headers = {"Authorization": f"Bearer {api_key}"}
    prompt = f"""你是一个严格的内容审核专家。请对以下 B 站视频的内容主题与监控关键词【{keyword}】的【实际核心相关度】进行高精度判决。
注意判决标准：
1. 必须是视频的【核心主体/主角/主要探讨对象】直接紧扣【{keyword}】。
2. 坚决排除：字面同形异义、偶发口癖口误、蹭标签、背景路过、缩写歧义（例如将笔记本电脑缩写为“本子”、CF游戏外号碰瓷“坏女人”等若脱离用户原意需严格辨析）。
3. 必须排除只在标题附带一两个字却讲其他事情的引流视频。

视频标题：《{title}》
视频简介：{desc[:200]}

请仅回复标准 JSON：
{{"is_relevant": true或false, "reason": "简明判定理由"}}"""

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 80,
        "temperature": 0.1
    }

    try:
        r = requests.post(f"{base_url.rstrip('/')}/chat/completions", headers=headers, json=payload, timeout=5)
        res = r.json()
        raw_text = res["choices"][0]["message"]["content"].strip()
        clean_json = raw_text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_json)
        return bool(data.get("is_relevant")), data.get("reason", "LLM 语义判定")
    except Exception as e:
        return True, f"LLM 校验降级 ({e})"

def check_video_relevance(
    video: Dict[str, Any],
    target_keyword: str,
    min_play: int = 500,
    min_duration_sec: int = 15,
    max_duration_sec: int = 2400,
    max_days_old: int = 7,
    min_relevance_score: float = 0.75,  # 提升词法基础门槛至 0.75 (大幅收敛候选集)
    short_video_play_threshold: int = 1500,
    visual_short_threshold: int = 3000,
    llm_verify: bool = True,
    llm_base_url: str = "http://127.0.0.1:8317/v1",
    llm_api_key: str = "EMPTY",
    llm_model: str = "gemini-3.8-flash-high"
) -> tuple[bool, str, float]:
    title = video.get("title", "")
    desc = video.get("desc", "")
    duration = video.get("duration", 0)
    play = video.get("play", 0)
    pubdate = video.get("pubdate", 0)

    # 1. 黑名单过滤
    for neg in NEGATIVE_KEYWORDS:
        if neg in title:
            return False, f"命中营销/引流黑名单: {neg}", 0.0

    # 2. 时效性检测
    if pubdate and pubdate > 0 and max_days_old > 0:
        now_ts = int(time.time())
        age_sec = now_ts - pubdate
        age_days = age_sec / 86400
        if age_days > max_days_old:
            return False, f"视频时效已过 ({age_days:.1f}天前发布 > 限限{max_days_old}天)", 0.0

    # 3. 词法关联度与跨词防碰撞检测 (纯正则，0 KB 常驻开销)
    score, match_reason = calculate_relevance_score(title, target_keyword)
    if score < min_relevance_score:
        return False, f"词法关联度不足 ({score:.2f} < {min_relevance_score}): {match_reason}", score

    # 4. 时长与质量区间过滤
    if duration > max_duration_sec:
        return False, f"视频过长 ({duration}s > {max_duration_sec}s)", score

    kw_lower = target_keyword.lower()
    title_lower = title.lower()
    is_visual_art = any(k in kw_lower or k in title_lower for k in VISUAL_ART_KEYWORDS)

    if is_visual_art:
        if duration > 0 and duration < 5:
            return False, f"视频过短 ({duration}s < 5s)", score
        if duration > 0 and duration < 15:
            if play and isinstance(play, int) and play < visual_short_threshold:
                return False, f"微短MMD播放量未达神作门槛 ({play} < {visual_short_threshold})", score
        elif duration >= 15 and duration < 60:
            if play and isinstance(play, int) and play < short_video_play_threshold:
                return False, f"短视频热度不足 ({play} < {short_video_play_threshold})", score
        else:
            if play and isinstance(play, int) and play < min_play:
                return False, f"播放量偏低 ({play} < {min_play})", score
    else:
        if duration > 0 and duration < min_duration_sec:
            return False, f"视频过短 ({duration}s < {min_duration_sec}s，信息量过低)", score
        if duration > 0 and duration < 60:
            if play and isinstance(play, int) and play < short_video_play_threshold:
                return False, f"短视频热度不足 ({play} < {short_video_play_threshold})", score
        else:
            if play and isinstance(play, int) and play < min_play:
                return False, f"播放量偏低 ({play} < {min_play})", score

    # 5. LLM 深度语义真伪判定
    if llm_verify and llm_api_key and llm_api_key != "EMPTY":
        is_rel, rel_reason = verify_semantic_relevance_with_llm(
            title=title,
            desc=desc,
            keyword=target_keyword,
            base_url=llm_base_url,
            api_key=llm_api_key,
            model=llm_model
        )
        if not is_rel:
            return False, f"LLM 深度语义拦截: {rel_reason}", score
        else:
            return True, f"OK (词法+LLM双重过审: {rel_reason})", score

    return True, f"OK ({match_reason}, 关联分: {score:.2f})", score
