#!/usr/bin/env python3
"""每日自检（由 systemd timer 拉起）。

  · 平时   跑 --quick（静态+组件+运行态，约 1 分钟，不消耗 GPU/云端额度）
  · 周日   跑全量（多出端到端那一段，约 4 分钟，会真的抽音频走转写）

与金丝雀的分工（**刻意不合并**）：
  · 金丝雀 15 秒，只盯两道闸门，失败会**自动动手**（关字幕通道）
  · 自检 1~4 分钟，覆盖四层，失败**只报警不动手**——
    因为失败原因可能是配置错、可能是日志有错，乱动手会做错事。

退出码：0=通过 / 1=有失败
"""
import datetime
import json
import os
import subprocess
import sys

PROJ = "/root/bili-summary-stream"
VENV = f"{PROJ}/.venv/bin/python"
STATE = f"{PROJ}/data/selftest_state.json"
ALERT_TO = os.environ.get("HERMES_ALERT_PROFILE", "default")


def save(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def alert(text):
    try:
        r = subprocess.run(["hermes", "send", "--to", ALERT_TO, text],
                           capture_output=True, text=True, timeout=120,
                           env={**os.environ, "HOME": "/root"})
        return r.returncode == 0
    except Exception:
        return False


def main():
    drill = "--drill" in sys.argv
    now = datetime.datetime.now()

    # 演练：直接演示"自检失败时会收到什么"，不真跑测试、不动任何东西
    if drill:
        stamp = now.strftime("%Y-%m-%d %H:%M:%S")
        body = (
            f"🧪【演练·非故障】\n"
            f"这是每日自检的**演练**，不是真的出事。\n"
            f"目的：确认报警能送达、文案能读懂。\n\n"
            f"演练用的失败项（示例）：\n"
            f"· [A3] llm.api_key 未配置\n"
            f"· [D4] 近 24 小时出现 3 条 error 级日志\n\n"
            f"真实失败时会执行：只发本条通知，**不改配置、不重启服务**。\n"
            f"时间：{stamp}"
        )
        ok = alert(body)
        print(f"[自检演练] 告警推送: {'成功' if ok else '失败'}")
        return 0

    is_sunday = now.weekday() == 6
    full = is_sunday or "--full" in sys.argv
    args = [VENV, f"{PROJ}/tools/self_test.py"] + ([] if full else ["--quick"])
    mode = "全量" if full else "精简"
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[每日自检] {stamp}  模式={mode}")

    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=900, cwd=PROJ)
        out = (r.stdout or "") + (r.stderr or "")
        rc = r.returncode
    except Exception as e:
        out, rc = f"自检执行异常: {e}", 1

    print(out)
    fails = [ln.strip() for ln in out.splitlines() if ln.startswith("  ✗")]

    if rc == 0:
        n_pass = sum(1 for ln in out.splitlines() if ln.startswith("  ✓"))
        print(f"✓ 自检通过（{n_pass} 项，模式={mode}）")
        save({"last_run": stamp, "mode": mode, "status": "ok",
              "passed": n_pass, "failing": []})
        return 0

    body = (
        f"🧪【自检异常】\n"
        f"B站推送脚本的每日自检未通过（模式：{mode}）。\n\n"
        f"失败项：\n" + "\n".join(f"· {x.lstrip('✗ ').strip()}" for x in (fails or ["（未能解析）"])) +
        f"\n\n时间：{stamp}\n"
        f"详情：journalctl -u bili-self-test -n 60"
    )
    if drill:
        body = "🧪【演练·非故障】\n" + body.split("\n", 1)[1]
    ok = alert(body)
    print(f"告警推送: {'成功' if ok else '失败'}")
    save({"last_run": stamp, "mode": mode, "status": "failed",
          "failing": fails, "alerted": ok})
    return 0 if drill else 1


if __name__ == "__main__":
    sys.exit(main())
