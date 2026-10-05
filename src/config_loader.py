import os
import re
import yaml
from pathlib import Path
from typing import Dict, Any

ENV_PATTERN = re.compile(r'\$\{([^}^{]+)\}')

def _replace_env_vars(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: _replace_env_vars(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_replace_env_vars(item) for item in data]
    elif isinstance(data, str):
        match = ENV_PATTERN.match(data)
        if match:
            env_var = match.group(1)
            return os.environ.get(env_var, "")
        return ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), data)
    return data

def load_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    p = Path(config_path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / config_path

    if not p.exists():
        raise FileNotFoundError(f"配置文件不存在: {p}")

    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return _replace_env_vars(raw)
