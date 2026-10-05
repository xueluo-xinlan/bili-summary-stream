from typing import Dict, Any
from openai import OpenAI

_SYSTEM_PROMPT_BASE = """你是一个专业的音视频内容深度分析与提炼专家。
你的任务是将 B 站视频的语音转写/字幕内容整理成一份兼具【深度洞察】与【结构化排版】的中文速读摘要。
要求：
1. 语言干练、客观犀利，去除废话、口癖、赞助广告词与口头过渡词。
2. 保持中文表达地道专业，保留专业技术或行业专有名词。
3. 突出核心论点之间的推导逻辑，而非流水账。
4. 严格按照指定的格式输出，便于移动端/私信快速阅读。
"""

_RULE_MISMATCH_GUARD = """5. 【最高优先级｜遇到内容与标题不符时的唯一正确反应】
   如果【语音转写/字幕内容】与【视频标题】【UP主】明显属于两回事
   （例如：标题讲游戏攻略，转写却在讲美食制作；标题是耳机评测，转写却在讲手办开箱），
   这**几乎一定是上游取到了错误的转写内容**，是数据故障，而**不是**视频本身的问题。

   ⛔ 绝对禁止：把它描述成"标题党""挂羊头卖狗肉""货不对板""标题严重虚标"，
      禁止质疑或贬低视频作者，禁止建议观众"划走""避坑"。
      —— 把自己的数据错误栽赃给无辜的创作者，是最不可接受的输出。

   ✅ 唯一正确的做法：只输出下面这一行，不要输出任何其他内容：
      __CONTENT_MISMATCH__
"""

# 可信来源（音视频由我们按 bvid+cid 亲自取回，内容必然属于该视频）下，
# 视频**确实**文不对题时的规则。此时不许吐标记——那会永远阻断推送——
# 而是中性披露后如实交付实际内容。
_RULE_NEUTRAL_DISCLOSURE = """5. 【最高优先级｜本视频的实际内容与标题主题不同——这是已核实的事实】
   ⚠️ 本条**取代**常规的"内容不符就报异常"规则：**不要输出 __CONTENT_MISMATCH__**，
      那会阻断本次推送。请照常输出一份完整的总结。
   正文第一行固定写：ℹ️ 本视频实际内容与标题主题不同｜以下为实际内容的提炼
   然后**如实提炼你收到的实际内容**——观众真正会看到、听到的东西。
   ⛔ 禁止指责任何人、禁止暗示造假：不得出现「标题党」「挂羊头卖狗肉」「货不对板」
      「虚假引流」「欺诈」「名不副实」「虚标」等任何指控性措辞。
   ⛔ 不得揣测作者动机，不得建议观众「划走」「避坑」「不要看」。
   ⛔ 不要复述标题做对比，也不要解释"标题为何如此"。
"""

SYSTEM_PROMPT = _SYSTEM_PROMPT_BASE + _RULE_MISMATCH_GUARD
SYSTEM_PROMPT_DISCLOSURE = _SYSTEM_PROMPT_BASE + _RULE_NEUTRAL_DISCLOSURE

# 内容与标题不符时摘要器的唯一合法输出；管线据此拦截而不是推送
MISMATCH_MARKER = "__CONTENT_MISMATCH__"

# 兜底启发式：即使模型没吐标记，出现这些措辞也说明它在为数据故障找借口
BLAME_PHRASES = (
    "标题党", "挂羊头卖狗肉", "货不对板", "标题虚标", "标题严重虚标",
    "标题与内容脱节", "与标题不符", "与耳机标题不符", "标题所指",
    "疑似误传", "标题宣称……实际", "标题所述", "名不副实",
    # 「标题宣称 X，实则为 Y」是最常见的甩锅句式之一。
    # 早期只写了带省略号的「标题宣称……实际」，对真实写法完全匹配不上——
    # 自检套件实测暴露了这个洞：该句式会整个漏过去。改按不含省略号的词根匹配。
    "标题宣称", "标题声称", "标题写的", "标题所说", "挂羊头",
)

# 甩锅句式永远是「标题说 X，实际是 Y」这种对比结构。
# 必须两个信号同时出现才判定——否则会误伤"盘点标题党套路"这类本来就
# 在讨论该话题的正常视频（它们会永远卡在待补推队列里，是静默损失）。
CONTRAST_MARKERS = (
    "实际内容为", "实际内容是", "实际内容却", "实际为", "实则为", "实则",
    "标题与内容", "不符", "脱节", "货不对板", "名不副实",
    "完全无", "完全缺失", "几乎无", "毫无", "音轨为", "音画",
)


# ── 来源判定：区分「我们的锅」与「视频本身如此」 ─────────────────────────
#
# 这是本次事故最关键的一条设计。同一个「内容与标题不符」，
# 出处不同，性质完全相反：
#
#   · 来源 = 字幕通道(official_subtitle)：B站字幕接口会返回**别的视频**的字幕，
#     所以不符几乎必然是我们的取源故障 → 必须拦下、换源重试。
#
#   · 来源 = 本机GPU / 云端ASR / 云端视觉：音视频是我们**按 bvid+cid 亲自取回**的，
#     内容必然属于该视频，不存在取错的可能 → 不符是**视频本身的属性**，
#     此时应当**如实交付实际内容**，但绝不允许对作者下判决。
#
TRUSTED_SOURCES = ("laptop_rtx_5060", "cloud_asr", "cloud_vision")

# 可信来源下、视频确实文不对题时的「中性披露」前缀。
# 只陈述观察到的事实，不评价动机，不使用任何指控性词汇。
NEUTRAL_DISCLOSURE_PREFIX = """【重要上下文｜必读】
本视频的**实际音视频内容**与其**标题**描述的主题不同。这一点已经核实：
内容来源是我们直接按该视频的 bvid/cid 抓取的真实音轨/画面，不存在取错的可能，
因此这是**视频自身的属性**，与系统故障无关。

请遵守以下铁律输出总结：
1. 正文第一行固定写：ℹ️ 本视频实际内容与标题主题不同｜以下为实际内容的提炼
2. 正文只做一件事：**如实提炼你收到的实际内容**（观众真正会看到/听到的东西）。
3. ⛔ 禁止指责任何人、禁止暗示造假。不得出现「标题党」「挂羊头卖狗肉」
   「货不对板」「虚假引流」「欺诈」「名不副实」「虚标」等任何指控性措辞。
4. ⛔ 不得揣测作者动机，不得建议观众「划走」「避坑」「不要看」。
5. ⛔ 不要复述标题做对比，也不要解释"标题为何如此"——那是平台的事，不是你的。

"""


def looks_like_mismatch_rationalisation(text: str) -> bool:
    """判断摘要是否在为「数据故障」找视频本身的借口（即第二次伤害）。

    触发条件（宁可放过个别，也不要误伤）：
      · 摘要器吐出了 __CONTENT_MISMATCH__ 标记；或
      · 同时出现「甩锅措辞」与「标题vs实际」的对比句式。
    """
    if not text:
        return False
    if MISMATCH_MARKER in text:
        return True
    has_blame = any(p in text for p in BLAME_PHRASES)
    has_contrast = any(m in text for m in CONTRAST_MARKERS)
    return has_blame and has_contrast

BRIEF_PROMPT_TEMPLATE = """请针对以下 B 站日常监控巡检视频，输出【极简极速版中文速览】。
控制在 250 字以内，严格单条私信即读即走，无需冗长展开。
注意：如果视频是 MMD / 3D 动画 / 纯音乐或极短微短片（人声语音极少或仅为背景音乐），请重点依据【标题】与【简介】提炼出该作品的“角色模型”、“渲染风格与曲目”以及“视觉亮点”。

【视频标题】：{title}
【UP 主】：{owner_name}
【时长】：{duration_str}
【简介】：{desc}

【语音转写/音轨特征】：
{content}

---
请严格按以下精炼格式输出：
🎯【核心主旨】：用 1 句话概括视频内容（如哪位角色/哪首名曲/何种题材）。
📌【3大核心要点/视觉亮点】：
• 亮点1：模型与动作表现
• 亮点2：光影质感与镜头编排
• 亮点3：音画卡点与整体氛围
💡【观赏建议】：一句话给观众的最佳欣赏或跟练/观影建议。
"""

USER_PROMPT_TEMPLATE = """请针对以下用户主动指定精读的 B 站视频内容进行【高深度全量精细提炼】：

【视频标题】：{title}
【UP 主】：{owner_name}
【时长】：{duration_str}
【视频简介】：{desc}

【语音转写与时间轴内容】：
{content}

---
请按以下格式输出（务必保持清晰、紧凑的 Markdown 格式）：

📌【一句话核心主旨】
> 用 1-2 句话高度概括视频最核心的论点或传达的关键价值。

💡【核心逻辑与论证脉络】
1. **[观点1]**：详细解释逻辑与支撑事实。
2. **[观点2]**：详细解释逻辑与支撑事实。
3. **[观点3]**：详细解释逻辑与支撑事实。
(根据内容复杂度提炼 3-5 点，层层递进)

⏱️【关键时间线干货】
- [mm:ss] 要点描述
- [mm:ss] 要点描述
- [mm:ss] 要点描述

🎯【提炼金句与行动建议】
- **价值洞察**：观众能获得的启发或结论。
- **延伸建议**：对该话题的思考或避坑指南。
"""

class LLMSummarizer:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8317/v1",
        api_key: str = "EMPTY",
        model: str = "gemini-3.8-flash-high",
        temperature: float = 0.3,
        max_tokens: int = 2000
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.client = OpenAI(base_url=self.base_url, api_key=self.api_key)

    def summarize(
        self,
        video_info: Dict[str, Any],
        transcription: Dict[str, Any],
        custom_requirement: str = "",
        detailed: bool = False,
        mismatch_disclosure: bool = False
    ) -> str:
        duration = video_info.get("duration", 0)
        dur_m, dur_s = divmod(duration, 60)
        dur_h, dur_m = divmod(dur_m, 60)
        if dur_h > 0:
            duration_str = f"{dur_h}小时{dur_m}分{dur_s}秒"
        else:
            duration_str = f"{dur_m}分{dur_s}秒"

        timeline_text = transcription.get("timeline_text", "")
        if not timeline_text:
            timeline_text = transcription.get("full_text", "")

        if len(timeline_text) > 35000:
            timeline_text = timeline_text[:35000] + "\n...(音频内容过长，后续内容已截断)..."

        template = USER_PROMPT_TEMPLATE if detailed else BRIEF_PROMPT_TEMPLATE
        user_content = template.format(
            title=video_info.get("title", ""),
            owner_name=video_info.get("owner_name", ""),
            duration_str=duration_str,
            desc=video_info.get("desc", "")[:300],
            content=timeline_text
        )

        if custom_requirement:
            user_content += f"\n\n【用户专属个性化关注重点】：\n{custom_requirement}\n请在总结时务必重点侧重于满足上述要求与视角进行分析提炼！"

        if mismatch_disclosure:
            user_content = NEUTRAL_DISCLOSURE_PREFIX + user_content

        max_tok = self.max_tokens if detailed else 600
        system_prompt = SYSTEM_PROMPT_DISCLOSURE if mismatch_disclosure else SYSTEM_PROMPT
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=self.temperature,
            max_tokens=max_tok
        )

        summary_text = response.choices[0].message.content.strip()
        return summary_text
