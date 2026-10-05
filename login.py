import os
import sys
import time
import requests
import qrcode
import yaml
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"
QR_PATH = BASE_DIR / "cache" / "bili_login_qr.png"

def generate_qrcode():
    QR_PATH.parent.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://passport.bilibili.com/login"
    }
    resp = requests.get(
        "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
        headers=headers,
        timeout=10
    )
    res = resp.json()
    if res.get("code") != 0:
        raise RuntimeError(f"获取登录二维码失败: {res.get('message')}")

    data = res["data"]
    qr_url = data["url"]
    qrcode_key = data["qrcode_key"]

    # 1. 生成并保存本地二维码图片
    img = qrcode.make(qr_url)
    img.save(QR_PATH)

    # 2. 生成终端纯文本 ASCII 二维码 (方便终端即时扫描)
    qr = qrcode.QRCode()
    qr.add_data(qr_url)
    qr.make(fit=True)
    
    return qrcode_key, QR_PATH, qr

def poll_status(qrcode_key: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://passport.bilibili.com/login"
    }
    url = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
    resp = requests.get(url, params={"qrcode_key": qrcode_key}, headers=headers, timeout=10)
    res = resp.json()
    return res, resp.cookies

def update_config(sessdata: str, bili_jct: str, dedeuserid: str):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    if "bilibili" not in cfg:
        cfg["bilibili"] = {}
    cfg["bilibili"]["sessdata"] = sessdata
    cfg["bilibili"]["bili_jct"] = bili_jct
    try:
        uid_int = int(dedeuserid)
    except Exception:
        uid_int = 0
    cfg["bilibili"]["sender_uid"] = uid_int

    # 保持已有的接收方列表不被覆盖
    if "notification" not in cfg:
        cfg["notification"] = {}
    if not cfg["notification"].get("receiver_uids") and not cfg["notification"].get("receiver_uid"):
        cfg["notification"]["receiver_uids"] = [1000001]

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)

    print(f"\n[✓] 凭据已成功写入: {CONFIG_PATH}")
    print(f"    - 小号发信 UID: {uid_int}")
    print(f"    - SESSDATA: {sessdata[:10]}...{sessdata[-6:]}")
    print(f"    - bili_jct (csrf): {bili_jct}")

def main():
    print("[*] 正在请求 B 站官方登录二维码...")
    qrcode_key, qr_img_path, qr_obj = generate_qrcode()
    print(f"[+] 二维码图片已生成: {qr_img_path.resolve()}")
    print("\n请使用手机 Bilibili App 扫描下方二维码登录：\n")
    qr_obj.print_ascii(invert=True)
    print("\n等待扫码确认中...")

    start_time = time.time()
    while time.time() - start_time < 180:
        res, cookies = poll_status(qrcode_key)
        code = res.get("data", {}).get("code")
        msg = res.get("data", {}).get("message")

        if code == 0:
            print("\n[🎉] 登录成功！正在提取 Cookie 凭据...")
            # 提取 cookie
            cookie_dict = cookies.get_dict()
            sessdata = cookie_dict.get("SESSDATA", "")
            bili_jct = cookie_dict.get("bili_jct", "")
            dedeuserid = cookie_dict.get("DedeUserID", "")
            
            # 如果未从 requests cookies 拿到，尝试从 url 参数或 headers 解析
            if not sessdata:
                # 从 Set-Cookie 字符串兜底
                for k, v in cookies.items():
                    if k == "SESSDATA": sessdata = v
                    elif k == "bili_jct": bili_jct = v
                    elif k == "DedeUserID": dedeuserid = v

            if sessdata and bili_jct:
                update_config(sessdata, bili_jct, dedeuserid)
            else:
                print(f"[!] 获取到的 Cookie 为: {cookie_dict}")
                print(f"[!] 未能解析出完整的 SESSDATA，请重试。")
            return
        elif code == 86101:
            # 未扫码
            pass
        elif code == 86090:
            print("[*] 二维码已扫描，请在手机端点击【确认登录】...", end="\r")
        elif code == 86038:
            print("\n[!] 二维码已超时失效，请重新运行脚本。")
            return
        else:
            print(f"[*] 状态: {msg} ({code})", end="\r")

        time.sleep(2)

    print("\n[!] 登录超时，请重试。")

if __name__ == "__main__":
    main()
