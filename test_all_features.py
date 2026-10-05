import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from src.config_loader import load_config
from src.bili_api import BiliClient
from src.filter import check_video_relevance
from src.summarizer import LLMSummarizer
from src.notifier import BiliNotifier
from src.storage import Storage

print("=== 功能全项自检开始 ===")
cfg = load_config("config.yaml")

# 1. 配置加载与多账号画像
b_cfg = cfg["bilibili"]
prof = cfg["profiles"]
assert "1000001" in prof, "大号1配置缺失"
assert "1000002" in prof, "大号2配置缺失"
k1 = len(prof["1000001"]["keywords"])
k2 = len(prof["1000002"]["keywords"])
print(f"[OK] 配置校验通过: 大号1关键词({k1}个), 大号2关键词({k2}个)")

# 2. B 站 Cookie 身份鉴权
client = BiliClient(b_cfg["sessdata"], b_cfg["bili_jct"], b_cfg["sender_uid"])
nav = client.session.get("https://api.bilibili.com/x/web-interface/nav", timeout=10).json()
assert nav.get("code") == 0, f"Cookie失效: {nav}"
print(f"[OK] B站Cookie有效: 登录身份 {nav['data']['uname']} (UID: {nav['data']['mid']})")

# 3. LLM API (两种模式总结输出测试)
summarizer = LLMSummarizer(cfg["llm"]["base_url"], cfg["llm"]["api_key"], cfg["llm"]["model"])
v_sample = {"title": "自检测试视频", "owner_name": "测试UP", "duration": 120, "desc": "自检测试简介"}
trans_sample = {"full_text": "这是一个全功能自动化自检测试用例文本。", "timeline_text": "[00:00 -> 01:00] 这是一个全功能自动化自检测试用例文本。"}

# 3.1 极简速读模式 (日常推送用)
brief_out = summarizer.summarize(v_sample, trans_sample, detailed=False)
assert len(brief_out) > 30 and "核心主旨" in brief_out, "极简总结格式不符"
print(f"[OK] 极简速读总结正常 (字数: {len(brief_out)})")

# 3.2 全量深度模式 (私信分享/@机器人用)
detail_out = summarizer.summarize(v_sample, trans_sample, detailed=True)
assert len(detail_out) > 50 and "核心主旨" in detail_out, "深度总结格式不符"
print(f"[OK] 全量精细深度总结正常 (字数: {len(detail_out)})")

# 4. 过滤引擎 (时效性、短视频质量、语义抗碰撞)
# 4.1 跨词撞车拦截测试 (蓝色情人节 vs 色情)
collision_v = {"title": "KickFlip《That‘s A No No+OneSpark+蓝色情人节》舞蹈", "duration": 180, "play": 10000, "pubdate": 1789200000}
ok_c, _, _ = check_video_relevance(collision_v, "色情", llm_verify=False)
assert not ok_c, "跨词碰撞拦截失效！"
print("[OK] 跨词语义碰撞拦截正常 (成功拦截蓝色情人节撞车色情)")

# 4.2 <15s 高质量 MMD 放行测试
mmd_v = {"title": "【原神MMD】雷电将军绝美卡点！", "duration": 12, "play": 50000, "pubdate": 1789200000}
ok_m, _, _ = check_video_relevance(mmd_v, "MMD", llm_verify=False)
assert ok_m, "高赞微短MMD放行失效！"
print("[OK] 优质微短 MMD (<15s) 智能放行正常")

# 5. 官方字幕云端降级提取
info = client.get_video_info("BV1QBYW6gERH")
sub_data = client.get_official_subtitles("BV1QBYW6gERH", info["cid"])
assert sub_data is not None and len(sub_data.get("full_text", "")) > 0, "官方字幕降级提取失败"
print(f"[OK] 云端官方字幕自愈降级链路正常 (成功获取字符数: {len(sub_data['full_text'])})")

# 6. 私信控制指令解析与响应测试
from src.bot_listener import BotListener
from src.pipeline import SummaryPipeline
pipeline = SummaryPipeline(cfg)
listener = BotListener(pipeline, cfg)
help_reply = listener._handle_direct_command(1000001, "查看设置")
assert help_reply is not None and "专属配置面板" in help_reply, "私信配置指令解析失败"
print("[OK] 私信纯中文交互指令解析正常")

print("\n=== 全部功能项实测通过！系统健康度 100% ===")
