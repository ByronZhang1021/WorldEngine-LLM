"""工具模块 — 图片生成、联网搜索。

图片生成：302.ai z-image-turbo API
"""
import asyncio
import json
import re
import ssl
import time
import urllib.request
import urllib.error
from pathlib import Path

from .utils import log, load_config, read_file, read_json, load_state, CHARACTERS_DIR, LOCATIONS_PATH, get_media_dir, PROMPTS_DIR, parse_character_file, load_character, get_turn_logger, load_temp_characters

RATIO_MAP = {
    "1:1":  (1024, 1024),
    "2:1":  (1024, 512),
    "16:9": (1024, 576),
    "4:3":  (1024, 768),
    "3:2":  (1024, 682),
    "2:3":  (682, 1024),
    "3:4":  (768, 1024),
    "9:16": (576, 1024),
}


def _api_request(url: str, payload: dict, timeout: int = 60) -> dict:
    """通用 302.ai API 请求。"""
    config = load_config()
    api_key = config["api"]["api_key"]
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _api_request_raw(url: str, payload: dict, timeout: int = 60) -> bytes:
    """API 请求，返回原始字节（用于下载图片等）。"""
    config = load_config()
    api_key = config["api"]["api_key"]
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read()


# ── 图片生成（对话自动触发） ──────────────────────────────


def _build_scene_prompt_input(description: str, characters: list[str], conversation: str = "") -> str:
    """构建图片 prompt LLM 的富输入。

    收集画面中角色的全部视觉相关数据 + 地点 + 时间 + 对话上下文。
    """
    parts = []

    for char_name in characters:
        char_loaded = False
        # 先尝试加载正式角色文件
        try:
            data = load_character(char_name)
            sections = parse_character_file(data)
            # 检查是否有实际内容（load_character 对不存在的文件返回空字典，不抛异常）
            has_content = any(sections[k].strip() for k in sections)
            if has_content:
                parts.append(f"【角色：{char_name}】")
                if sections["public_base"].strip():
                    parts.append(f"基础设定：\n{sections['public_base']}")
                if sections["private_base"].strip():
                    parts.append(f"私密设定（可能影响外观的）：\n{sections['private_base']}")
                if sections["public_dynamic"].strip():
                    parts.append(f"当前外在状态（别人看得到的）：\n{sections['public_dynamic']}")
                if sections["private_dynamic"].strip():
                    parts.append(f"其他状态（可能影响外观的）：\n{sections['private_dynamic']}")
                char_loaded = True
        except Exception:
            pass

        # 正式角色文件无数据，尝试从临时角色数据中查找
        if not char_loaded:
            try:
                temp_chars = load_temp_characters()
                # 精确匹配
                tc = temp_chars.get(char_name)
                # 模糊匹配：名字出现在临时角色的 description 中（如 "小芸" 匹配 "女服务员" 的描述 "名小芸"）
                if not tc:
                    for tc_name, tc_data in temp_chars.items():
                        desc = tc_data.get("description", "")
                        if char_name in desc or char_name in tc_name:
                            tc = tc_data
                            break
                if tc:
                    parts.append(f"【角色：{char_name}】（临时角色）")
                    if tc.get("description"):
                        parts.append(f"外貌描述：{tc['description']}")
                    if tc.get("state"):
                        parts.append(f"当前状态：{tc['state']}")
                else:
                    log("warning", f"图片生成: 角色 [{char_name}] 既无角色文件也无临时角色数据")
            except Exception as e2:
                log("warning", f"加载临时角色数据失败 [{char_name}]: {e2}")

    # 地点和时间
    state = load_state()
    current_time = state.get("current_time", "")

    # 获取角色当前位置
    location_name = ""
    if characters:
        char_state = state.get("characters", {}).get(characters[0], {})
        location_name = char_state.get("location", "")

    # 地点描述
    loc_desc = ""
    if location_name and LOCATIONS_PATH.exists():
        try:
            locations_data = read_json(LOCATIONS_PATH)
            for loc in locations_data:
                if loc.get("name") == location_name:
                    loc_desc = loc.get("description", "")
                    break
        except Exception:
            pass

    parts.append(f"\n场景描述：{description}")
    loc_info = location_name
    if loc_desc:
        loc_info += f" — {loc_desc}"
    parts.append(f"地点：{loc_info}")
    parts.append(f"时间：{current_time}")

    if conversation and conversation.strip():
        # 只取最后 500 字作为上下文
        parts.append(f"\n最近对话：\n{conversation[-500:]}")

    return "\n\n".join(parts)


def _generate_scene_image_sync(description: str, characters: list[str], conversation: str = "") -> str | None:
    """为对话场景生成图片（同步，在线程中运行）。

    流程：
    1. 构建富输入（角色外貌+动态记忆+地点+时间+对话）
    2. 调用 LLM 生成英文 prompt + 宽高比
    3. 调用 z-image-turbo 生图
    4. 下载保存，返回本地路径
    """
    config = load_config()
    base_url = config["api"]["base_url"]
    api_key = config["api"]["api_key"]

    prompt_input = ""
    prompt = ""
    ratio = ""

    try:
        # Step 1: 构建富输入
        prompt_input = _build_scene_prompt_input(description, characters, conversation)
        log("info", f"场景图片: 角色={characters}, 描述={description[:50]}...")

        # Step 2: 调用 LLM 生成英文 prompt
        system_msg = read_file(PROMPTS_DIR / "image_gen.md")
        llm_model = config.get("models", {}).get("chat", {}).get("model", "grok-4-1-fast-reasoning")

        body = json.dumps({
            "model": llm_model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt_input},
            ],
            "max_tokens": 2000,
            "temperature": 0.4,
            "stream": False,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{base_url}/v1/chat/completions",
            data=body, method="POST", headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
        )
        ctx = ssl.create_default_context()
        t0_llm = time.time()
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            result = json.loads(resp.read())
        elapsed_llm = time.time() - t0_llm

        # 记录 LLM 调用到 TurnLogger
        usage = result.get("usage", {})
        tl = get_turn_logger()
        if tl:
            tl.record_llm_call(
                "图片Prompt", llm_model, elapsed_llm,
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
            )

        raw = result["choices"][0]["message"].get("content", "").strip()
        if not raw:
            raw = result["choices"][0]["message"].get("reasoning_content", "").strip()
        # 去掉 markdown 代码块
        if raw.startswith("```"):
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)

        try:
            parsed = json.loads(raw)
            prompt = parsed["prompt"]
            ratio = parsed.get("aspect_ratio", "3:4")
            if ratio not in RATIO_MAP:
                ratio = "3:4"
        except (json.JSONDecodeError, KeyError):
            prompt = raw
            ratio = "3:4"

        log("info", f"场景图片 prompt: {prompt[:100]}... ratio={ratio}")

        # Step 3: 调用 z-image-turbo
        img_cfg = config.get("models", {}).get("image", {})
        img_model = img_cfg.get("model", "z-image-turbo")
        w, h = RATIO_MAP[ratio]
        payload = {
            "prompt": prompt,
            "image_size": {"width": w, "height": h},
            "num_inference_steps": img_cfg.get("num_inference_steps", 6),
            "enable_safety_checker": False,
            "output_format": "jpeg",
        }

        url = f"{base_url}/302/submit/z-image-turbo"
        t0_img = time.time()
        img_result = _api_request(url, payload, timeout=120)
        elapsed_img = time.time() - t0_img

        # 记录生图调用到 TurnLogger（按次计费，0 tokens）
        tl = get_turn_logger()
        if tl:
            tl.record_llm_call("图片生成", img_model, elapsed_img, 0, 0)

        images = img_result.get("images", [])
        if not images:
            log("warning", f"场景图片: z-image-turbo 未返回图片: {img_result}")
            return None

        image_url = images[0].get("url", "")
        if not image_url:
            log("warning", f"场景图片: z-image-turbo 无 URL: {img_result}")
            return None

        # Step 4: 下载图片
        req = urllib.request.Request(image_url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; WorldEngine/1.0)",
        })
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            img_data = resp.read()

        img_dir = get_media_dir()
        filepath = img_dir / f"scene_{int(time.time())}.jpg"
        filepath.write_bytes(img_data)
        log("info", f"场景图片生成完成: {filepath} ({len(img_data)} bytes)")

        # 记录到 TurnLogger
        tl = get_turn_logger()
        if tl:
            tl.log_image_generation(
                character=characters[0] if characters else "?",
                description=description,
                image_characters=characters,
                prompt_input=prompt_input,
                english_prompt=prompt,
                aspect_ratio=ratio,
                image_path=str(filepath),
            )

        return str(filepath)

    except Exception as e:
        log("warning", f"场景图片生成失败: {e}")
        # 记录失败到 TurnLogger
        tl = get_turn_logger()
        if tl:
            tl.log_image_generation(
                character=characters[0] if characters else "?",
                description=description,
                image_characters=characters,
                prompt_input=prompt_input,
                english_prompt=prompt,
                aspect_ratio=ratio,
                error=str(e),
            )
        return None


# ── 联网搜索 ─────────────────────────────────────────────


def _web_search_sync(query: str) -> str | None:
    """同步执行联网搜索（在线程池中运行）。"""
    config = load_config()
    base_url = config["api"]["base_url"]
    model = config.get("models", {}).get("chat", {}).get("model", "grok-4-1-fast-reasoning")

    url = f"{base_url}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": f"搜索并简要回答：{query}"}
        ],
        "max_tokens": 500,
        "stream": False,
    }

    try:
        result = _api_request(url, payload)
        content = result["choices"][0]["message"]["content"]
        log("info", f"联网搜索完成: {query[:30]}")
        return content
    except Exception as e:
        log("warning", f"联网搜索失败: {e}")
        return None


async def web_search(query: str) -> str | None:
    """联网搜索（异步包装，不阻塞事件循环）。"""
    return await asyncio.to_thread(_web_search_sync, query)
