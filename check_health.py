import sys
import os
import json
import time
from pathlib import Path

# 强制 UTF-8 输出，防止 Windows GBK 终端报错
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from src.config_loader import load_config
from src.bili_api import BiliClient
from src.storage import Storage

print('=== 1. 配置文件检查 ===')
cfg = load_config('config.yaml')
b_cfg = cfg.get('bilibili', {})
notify_cfg = cfg.get('notification', {})
profiles = cfg.get('profiles', {})

print(f'发信小号 UID: {b_cfg.get("sender_uid")}')
print(f'接收大号列表: {notify_cfg.get("receiver_uids")}')
print(f'已配置画像的大号: {list(profiles.keys())}')

print('\n=== 2. B 站 Cookie 凭据有效性检查 ===')
client = BiliClient(b_cfg.get('sessdata', ''), b_cfg.get('bili_jct', ''), str(b_cfg.get('sender_uid', '')))
resp = client.session.get('https://api.bilibili.com/x/web-interface/nav', timeout=10).json()
code = resp.get('code')
if code == 0:
    data = resp.get('data', {})
    print(f'[OK] Cookie 有效！登录身份: {data.get("uname")} (UID: {data.get("mid")})')
else:
    print(f'[FAIL] Cookie 失效或异常: code={code}, message={resp.get("message")}')

print('\n=== 3. LLM API 端点连通性检查 ===')
llm_cfg = cfg.get('llm', {})
try:
    from openai import OpenAI
    llm_client = OpenAI(base_url=llm_cfg.get('base_url'), api_key=llm_cfg.get('api_key'))
    test_res = llm_client.chat.completions.create(
        model=llm_cfg.get('model'),
        messages=[{'role': 'user', 'content': 'hi'}],
        max_tokens=5
    )
    print(f'[OK] LLM 端点连通正常！模型: {llm_cfg.get("model")}')
except Exception as e:
    print(f'[FAIL] LLM 端点调用异常: {e}')

print('\n=== 4. GPU / Whisper 依赖环境检查 ===')
try:
    import ctranslate2
    print(f'[OK] CTranslate2 CUDA 设备数量: {ctranslate2.get_cuda_device_count()}')
except Exception as e:
    print(f'[FAIL] CTranslate2 CUDA 检查异常: {e}')

print('\n=== 5. 最近处理历史记录 ===')
storage = Storage(cfg.get('paths', {}).get('history_db', 'data/history.db'))
rows = storage.get_recent_processed(limit=5)
print(f'最近记录数: {len(rows)}')
for r in rows:
    t_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r['processed_at']))
    print(f'  - [{t_str}] {r["bvid"]} | {r["source_type"]} | {r["title"][:25]}')

print('\n=== 6. 进程运行状态检查 ===')
import subprocess
try:
    out = subprocess.check_output(['powershell', 'Get-CimInstance Win32_Process -Filter "CommandLine like \'%main.py watch%\' and name=\'python.exe\'" | Select-Object ProcessId, @{Name=\'WorkingSet(MB)\';Expression={[math]::Round($_.WorkingSetSize/1MB,2)}} | Format-Table -AutoSize'], text=True)
    if out.strip():
        print(out.strip())
    else:
        print('[!] 当前主守护进程 (main.py watch) 未在运行！')
except Exception as e:
    print(f'检查进程失败: {e}')
