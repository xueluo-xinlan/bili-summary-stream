import sys
import time
import threading
import argparse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from src.config_loader import load_config
from src.pipeline import SummaryPipeline
from src.notifier import BiliNotifier
from src.storage import Storage
from src.bot_listener import BotListener

def extract_bvid(target: str) -> str:
    target = target.strip()
    if "bilibili.com/video/" in target:
        part = target.split("bilibili.com/video/")[1]
        bvid = part.split("/")[0].split("?")[0]
        return bvid
    if "b23.tv/" in target:
        import requests
        try:
            r = requests.get(target, allow_redirects=False, timeout=5)
            loc = r.headers.get("Location", "")
            if "bilibili.com/video/" in loc:
                part = loc.split("bilibili.com/video/")[1]
                return part.split("/")[0].split("?")[0]
        except Exception:
            pass
    if target.startswith("BV") or target.startswith("bv"):
        return target
    return target

def cmd_summarize(args, config):
    pipeline = SummaryPipeline(config)
    bvid = extract_bvid(args.bvid)
    print(f"[*] 准备处理单个视频: {bvid}")
    pipeline.process_bvid(bvid, source_type="manual", force=args.force)

def cmd_scan_profiles(args, config):
    pipeline = SummaryPipeline(config)
    print("[*] 触发多账号独立关键词与个性化巡检...")
    pipeline.scan_and_process_profiles()

def cmd_watch(args, config):
    pipeline = SummaryPipeline(config)
    listener = BotListener(pipeline, config)
    interval_min = config.get("daemon", {}).get("poll_interval_minutes", 30)

    # 1. 启动交互式私信/@后台监听线程
    t = threading.Thread(target=listener.run_polling_loop, daemon=True)
    t.start()
    print("[✓] 云端交互式私信分享与评论区@监听后台线程已就绪")

    # 2. 主线程负责多账号独立定时巡检
    print(f"[+] 启动定时守护主进程，每隔 {interval_min} 分钟进行一次多账号关键词个性化巡检...")
    try:
        while True:
            print(f"\n[>>> 定时巡检触发: {time.strftime('%Y-%m-%d %H:%M:%S')} <<<]")
            pipeline.scan_and_process_profiles()
            # 巡检后补推此前未取得真实文本的视频（幂等，成功即出队）
            pipeline.retry_pending()
            print(f"[*] 巡检完成，休眠 {interval_min} 分钟 (期间私信与@交互实时响应，按 Ctrl+C 退出)...")
            time.sleep(interval_min * 60)
    except KeyboardInterrupt:
        print("\n[!] 常驻守护进程已停止。")

def cmd_test_notify(args, config):
    bili_cfg = config.get("bilibili", {})
    notify_cfg = config.get("notification", {})
    targets = notify_cfg.get("receiver_uids") or notify_cfg.get("receiver_uid") or []
    if isinstance(targets, (int, str)):
        targets = [targets]
    targets = [int(u) for u in targets if u]

    if not targets:
        print("[!] 错误: 尚未在 config.yaml 中配置 notification.receiver_uids")
        return

    notifier = BiliNotifier(
        sessdata=bili_cfg.get("sessdata", ""),
        bili_jct=bili_cfg.get("bili_jct", ""),
        sender_uid=bili_cfg.get("sender_uid", 0),
        receiver_uids=targets,
        max_chunk_size=notify_cfg.get("max_chunk_size", 700)
    )

    test_msg = args.text or f"🔔【BiliSummaryStream 云端测试】私信推送链路正常，测试时间: {time.strftime('%Y-%m-%d %H:%M:%S')}"
    print(f"[*] 正在尝试向 {len(targets)} 个接收账号发送测试私信: {targets} ...")
    for t_uid in targets:
        try:
            notifier.send_raw_text(t_uid, test_msg)
            print(f"  [✓] 成功发送给 UID: {t_uid}")
        except Exception as e:
            print(f"  [✗] 发送给 UID: {t_uid} 失败: {e}")
        time.sleep(1.0)

def cmd_history(args, config):
    storage = Storage(config.get("paths", {}).get("history_db", "data/history.db"))
    rows = storage.get_recent_processed(limit=args.limit)
    if not rows:
        print("[-] 暂无处理历史记录")
        return
    print(f"\n{'BVID/任务KEY':<24} | {'时间':<19} | {'来源':<14} | {'UP 主':<12} | 标题")
    print("-" * 85)
    for r in rows:
        t_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r['processed_at']))
        print(f"{r['bvid']:<24} | {t_str:<19} | {r['source_type']:<14} | {r['owner_name'][:10]:<12} | {r['title'][:20]}")
    print()

def main():
    parser = argparse.ArgumentParser(
        description="Bilibili 视频深度总结与多账号个性化推送流 (云边协同 7x24h 架构)"
    )
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_sum = subparsers.add_parser("summarize", help="总结指定视频并推送到 B 站私信")
    p_sum.add_argument("--bvid", required=True, help="视频 BV 号或视频完整 URL")
    p_sum.add_argument("--force", action="store_true", help="忽略历史去重缓存，强制重新处理")

    subparsers.add_parser("scan-profiles", help="多账号独立巡检")
    subparsers.add_parser("watch", help="启动常驻云端守护")

    p_test = subparsers.add_parser("test-notify", help="测试私信推送链路")
    p_test.add_argument("--text", default=None, help="自定义测试消息")

    p_hist = subparsers.add_parser("history", help="查看历史记录")
    p_hist.add_argument("--limit", type=int, default=20, help="显示条数")

    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except Exception as e:
        print(f"[!] 读取配置文件失败: {e}")
        sys.exit(1)

    if args.command == "summarize":
        cmd_summarize(args, config)
    elif args.command == "scan-profiles":
        cmd_scan_profiles(args, config)
    elif args.command == "watch":
        cmd_watch(args, config)
    elif args.command == "test-notify":
        cmd_test_notify(args, config)
    elif args.command == "history":
        cmd_history(args, config)

if __name__ == "__main__":
    main()
