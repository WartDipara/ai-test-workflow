"""
探测 settings.yaml 中的 LLM 是否可用（与 game_agent 使用同一网关）。

用法:
  python test.py              # 文本探针：主脑 + 多模态
  python test.py --vision     # 额外测多模态看图（需同目录 image.png）
"""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

from openai import OpenAI

_REPO = Path(__file__).resolve().parent
_SETTINGS = _REPO / "config" / "settings.yaml"


def _load_sections():
    from game_agent.config.loader import load_app_config

    cfg = load_app_config(_SETTINGS)
    return cfg.llm, cfg.llm_multimodal


def _client(base_url: str, api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=base_url)


def _probe_text(label: str, base_url: str, api_key: str, model: str) -> bool:
    print(f"\n{'=' * 60}")
    print(f"[{label}] model={model}")
    print(f"        base_url={base_url}")
    client = _client(base_url, api_key)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "回复 OK 两个字母即可。"}],
            max_tokens=16,
        )
        content = (resp.choices[0].message.content or "").strip()
        print(f"  状态: 成功")
        print(f"  回复: {content!r}")
        return True
    except Exception as e:
        print(f"  状态: 失败")
        print(f"  错误: {type(e).__name__}: {e}")
        if "no healthy deployments" in str(e).lower():
            print("  说明: 网关 LiteLLM 侧该模型组无健康节点，需换 model_name 或联系网关管理员。")
        return False


def _image_to_data_url(path: Path) -> str:
    raw = path.read_bytes()
    b64 = base64.standard_b64encode(raw).decode("ascii")
    suffix = path.suffix.lower()
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix, "image/png")
    return f"data:{mime};base64,{b64}"


def _probe_vision(label: str, base_url: str, api_key: str, model: str, image_path: Path) -> bool:
    print(f"\n{'=' * 60}")
    print(f"[{label} 多模态] model={model} image={image_path.name}")
    if not image_path.is_file():
        print(f"  跳过: 找不到 {image_path}")
        return False
    client = _client(base_url, api_key)
    data_url = _image_to_data_url(image_path)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "用一句话描述图片内容。"},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            max_tokens=128,
        )
        content = (resp.choices[0].message.content or "").strip()
        print(f"  状态: 成功")
        print(f"  回复: {content[:300]!r}")
        return True
    except Exception as e:
        print(f"  状态: 失败")
        print(f"  错误: {type(e).__name__}: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="探测 settings.yaml 中的 LLM 网关")
    parser.add_argument(
        "--vision",
        action="store_true",
        help="额外测试 llm_multimodal 看图（需仓库根目录 image.png）",
    )
    args = parser.parse_args()

    if not _SETTINGS.is_file():
        print(f"错误: 找不到 {_SETTINGS}", file=sys.stderr)
        return 2

    llm, llm_mm = _load_sections()
    print("从 config/settings.yaml 加载配置")
    print(f"  主脑 llm.model_name = {llm.model_name}")
    print(f"  多模态 llm_multimodal = {llm_mm.model_name if llm_mm else '(未配置，沿用主脑)'}")

    ok_main = _probe_text("主脑 / keywizard", llm.base_url, llm.api_key, llm.model_name)

    if llm_mm:
        mm = llm_mm
        ok_mm = _probe_text("多模态 / screen_monitor", mm.base_url, mm.api_key, mm.model_name)
    else:
        ok_mm = _probe_text("多模态(回退主脑)", llm.base_url, llm.api_key, llm.model_name)

    ok_vision = True
    if args.vision:
        mm = llm_mm or llm
        ok_vision = _probe_vision(
            "多模态",
            mm.base_url,
            mm.api_key,
            mm.model_name,
            _REPO / "image.png",
        )

    print(f"\n{'=' * 60}")
    print("汇总:")
    print(f"  主脑 DeepSeek 路径: {'通过' if ok_main else '失败'}")
    print(f"  多模态文本:         {'通过' if ok_mm else '失败'}")
    if args.vision:
        print(f"  多模态看图:         {'通过' if ok_vision else '失败'}")

    if not ok_main:
        print(
            "\n按键精灵阶段依赖主脑模型；若主脑失败，game_agent 会在第一轮 agent.run 就退出。"
            "请更换 llm.model_name 或修复网关部署。"
        )

    all_ok = ok_main and ok_mm and ok_vision
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
