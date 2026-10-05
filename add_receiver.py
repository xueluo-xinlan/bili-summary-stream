import os
import sys
import time
import requests
import qrcode
import yaml
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"
QR_PATH = BASE_DIR / "cache" / "add_receiver_qr.png"

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
        raise RuntimeError(f"获取二维码失败: {res.get('message')}")

    data = res["data"]
    qr_url = data["url"]
    qrcode_key = data["qrcode_key"]

    img = qrcode.make(qr_url)
    img.save(QR_PATH)

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

def append_receiver_uid(uid: int):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    if "notification" not in cfg:
        cfg["notification"] = {}

    uids = cfg["notification"].get("receiver_uids", [])
    if isinstance(uids, int):
        uids = [uids]
    elif not isinstance(uids, list):
        uids = []

    if uid not in uids:
        uids.append(uid)
        cfg["notification"]["receiver_uids"] = uids
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        print(f"\n[✓] 成功接入接收方 UID: {uid}")
        print(f"    当前完整接收列表: {uids}")
    else:
        print(f"\n[i] UID: {uid} 已在接收列表中，无需重复添加")

def main():
    print("[*] 正在生成第二接收账号扫码二维码...")
    qrcode_key, qr_img_path, qr_obj = generate_qrcode()
    print(f"[+] 二维码已就绪: {qr_img_path.resolve()}")
    print("\n请使用【第二个 B 站账号】的手机 App 扫码以识别 UID：\n")
    qr_obj.print_ascii(invert=True)
    print("\n等待扫码识别中...")

    start_time = time.time()
    while time.time() - start_time < 180:
        res, cookies = poll_status(qrcode_key)
        code = res.get("data", {}).get("code")
        msg = res.get("data", {}).get("message")

        if code == 0:
            cookie_dict = cookies.get_dict()
            dedeuserid = cookie_dict.get("DedeUserID", "")
            if not dedeuserid:
                for k, v in cookies.items():
                    if k == "DedeUserID":
                        dedeuserid = v
            if dedeuserid:
                append_receiver_uid(int(dedeuserid))
            else:
                print(f"\n[!] 未能解析出 UID，请重试。")
            return
        elif code == 86101:
            pass
        elif code == 86090:
            print("[*] 二维码已扫描，请在手机端点击【确认登录】...", end="\r")
        elif code == 86038:
            print("\n[!] 二维码已超时，请重新运行。")
            return
        time.sleep(2)

    print("\n[!] 扫码超时。")

if __name__ == "__main__":
    main()
