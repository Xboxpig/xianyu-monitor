import json
import os
import re
import sys
from typing import Awaitable, Callable, Optional

import aiofiles

from src.infrastructure.external.ai_client import AIClient

# The meta-prompt to instruct the AI
META_PROMPT_TEMPLATE = """
你是一位世界级的AI提示词工程大师。你的任务是根据用户提供的【购买需求】，模仿一个【参考范例】，为闲鱼监控机器人的AI分析模块（代号 EagleEye）生成一份全新的【分析标准】文本。

你的输出必须严格遵循【参考范例】的结构、语气和核心原则，但内容要完全针对用户的【购买需求】进行定制。最终生成的文本将作为AI分析模块的思考指南。

---
这是【参考范例】（`macbook_criteria.txt`）：
```text
{reference_text}
```
---

这是用户的【购买需求】：
```text
{user_description}
```
---

请现在开始生成全新的【分析标准】文本。请注意：
1.  **只输出新生成的文本内容**，不要包含任何额外的解释、标题或代码块标记。
2.  保留范例中的 `[V6.3 核心升级]`、`[V6.4 逻辑修正]` 等版本标记，这有助于保持格式一致性。
3.  将范例中所有与 "MacBook" 相关的内容，替换为与用户需求商品相关的内容。
4.  思考并生成针对新商品类型的“一票否决硬性原则”和“危险信号清单”。
"""

ProgressCallback = Callable[[str, str], Awaitable[None]]

# 生成的分析标准完整性校验：参考范例约 1500+ 字符，且包含四个固定段落标记。
# 历史经验：max_output_tokens 过小会导致输出在句子中间被截断，产生残缺标准。
MIN_CRITERIA_LENGTH = 400
REQUIRED_CRITERIA_MARKERS = (
    ("第一部分", "第一部分：核心分析原则"),
    ("一票否决", "一票否决硬性原则"),
    ("第二部分", "第二部分：详细分析指南"),
    ("危险信号", "危险信号清单"),
)
CRITERIA_GENERATION_ATTEMPTS = 3


def validate_generated_criteria(text: str) -> list:
    """校验 AI 生成的分析标准是否完整，返回问题列表（空列表表示通过）。"""
    problems = []
    content = (text or "").strip()
    if len(content) < MIN_CRITERIA_LENGTH:
        problems.append(
            f"长度过短（{len(content)} 字符 < {MIN_CRITERIA_LENGTH}），疑似输出被截断"
        )
    for marker, label in REQUIRED_CRITERIA_MARKERS:
        if marker not in content:
            problems.append(f"缺少段落标记: {label}")
    return problems


async def _report_progress(
    progress_callback: Optional[ProgressCallback],
    step_key: str,
    message: str,
) -> None:
    if progress_callback:
        await progress_callback(step_key, message)


def _read_reference_text(reference_file_path: str) -> str:
    try:
        with open(reference_file_path, "r", encoding="utf-8") as file:
            return file.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"参考文件未找到: {reference_file_path}")
    except IOError as exc:
        raise IOError(f"读取参考文件失败: {exc}")


async def _request_generated_text(ai_client: AIClient, prompt: str) -> str:
    print("正在调用AI生成新的分析标准，请稍候...")
    try:
        generated_text = await ai_client._call_ai(
            [{"role": "user", "content": prompt}],
            temperature=0.5,
            max_output_tokens=4000,
            enable_json_output=False,
        )
    except Exception as exc:
        print(f"调用 OpenAI API 时出错: {exc}")
        raise

    print("AI已成功生成内容。")
    return generated_text.strip()


async def _close_ai_client(
    ai_client: AIClient,
    active_error: BaseException | None,
) -> None:
    try:
        await ai_client.close()
    except Exception as close_error:
        print(f"关闭 AI 客户端时出错: {close_error}")
        if active_error is None:
            raise


async def generate_criteria(
    user_description: str,
    reference_file_path: str,
    progress_callback: Optional[ProgressCallback] = None,
) -> str:
    """
    Generates a new criteria file content using AI.
    """
    ai_client = AIClient()
    active_error: BaseException | None = None
    try:
        if not ai_client.is_available():
            ai_client.refresh()
        if not ai_client.is_available():
            raise RuntimeError("AI客户端未初始化，无法生成分析标准。请检查.env配置。")

        await _report_progress(progress_callback, "reference", "正在读取参考文件。")
        print(f"正在读取参考文件: {reference_file_path}")
        reference_text = _read_reference_text(reference_file_path)

        await _report_progress(progress_callback, "prompt", "正在构建发送给 AI 的指令。")
        print("正在构建发送给AI的指令...")
        prompt = META_PROMPT_TEMPLATE.format(
            reference_text=reference_text,
            user_description=user_description,
        )

        await _report_progress(progress_callback, "llm", "正在调用 AI 生成分析标准。")
        last_problems: list = []
        for attempt in range(1, CRITERIA_GENERATION_ATTEMPTS + 1):
            generated_text = await _request_generated_text(ai_client, prompt)
            last_problems = validate_generated_criteria(generated_text)
            if not last_problems:
                return generated_text
            print(
                f"⚠️ 第 {attempt}/{CRITERIA_GENERATION_ATTEMPTS} 次生成的分析标准不完整: "
                f"{'; '.join(last_problems)}"
            )
            if attempt < CRITERIA_GENERATION_ATTEMPTS:
                await _report_progress(
                    progress_callback,
                    "llm",
                    f"第 {attempt} 次生成不完整，正在重试（{attempt + 1}/"
                    f"{CRITERIA_GENERATION_ATTEMPTS}）...",
                )
                print("正在重新调用AI生成分析标准...")
        raise RuntimeError(
            f"AI 生成的分析标准在 {CRITERIA_GENERATION_ATTEMPTS} 次尝试后仍不完整: "
            f"{'; '.join(last_problems)}"
        )
    except Exception as exc:
        active_error = exc
        raise
    finally:
        await _close_ai_client(ai_client, active_error)


SEARCH_PARAMS_EXTRACTION_PROMPT = """
你是一个搜索参数提取器。请从用户的【购买需求】中提取用于闲鱼搜索的结构化参数。

要求：
1. min_price / max_price：仅在需求中明确给出预算数字时填写（单位：元），否则填 null。
2. exclude_keywords：需求中明确排除的特征词（例如“不要二手”→["二手"]；“只要国行”→["非国行", "海外版"]），没有则填空数组 []。
3. 只输出 JSON 对象，不要任何额外文字。

【购买需求】：
{user_description}
"""

DEFAULT_SEARCH_PARAMS = {
    "min_price": None,
    "max_price": None,
    "exclude_keywords": [],
}


def _parse_search_params_json(text: str) -> dict:
    """把 AI 输出解析为结构化参数 dict，容忍 ```json 代码块标记。"""
    content = (text or "").strip()
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.S)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("提取结果不是 JSON 对象")
    return data


async def extract_search_params(
    user_description: str,
    ai_client: Optional[AIClient] = None,
) -> dict:
    """从购买需求中提取结构化搜索参数（价格区间/排除词）。

    设计为「尽力而为」：提取失败时返回空参数，不阻断任务生成。
    """
    params = dict(DEFAULT_SEARCH_PARAMS)
    owns_client = ai_client is None
    if ai_client is None:
        ai_client = AIClient()
    try:
        if not ai_client.is_available():
            ai_client.refresh()
        if not ai_client.is_available():
            raise RuntimeError("AI客户端未初始化，无法提取搜索参数。")
        raw = await ai_client._call_ai(
            [
                {
                    "role": "user",
                    "content": SEARCH_PARAMS_EXTRACTION_PROMPT.format(
                        user_description=user_description
                    ),
                }
            ],
            temperature=0.2,
            max_output_tokens=1000,
            enable_json_output=True,
        )
        data = _parse_search_params_json(raw)
        for key in ("min_price", "max_price"):
            if key in data:
                value = data[key]
                if value is None or isinstance(value, (int, float)):
                    params[key] = value
        if isinstance(data.get("exclude_keywords"), list):
            params["exclude_keywords"] = [
                str(item).strip()
                for item in data["exclude_keywords"]
                if str(item).strip()
            ]
        return params
    except Exception as exc:
        print(f"⚠️ 从需求提取搜索参数失败（将跳过回流）: {exc}")
        return dict(DEFAULT_SEARCH_PARAMS)
    finally:
        if owns_client and ai_client is not None:
            await ai_client.close()


async def update_config_with_new_task(new_task: dict, config_file: str = "config.json"):
    """
    将一个新任务添加到指定的JSON配置文件中。
    """
    print(f"正在更新配置文件: {config_file}")
    try:
        # 读取现有配置
        config_data = []
        if os.path.exists(config_file):
            async with aiofiles.open(config_file, 'r', encoding='utf-8') as f:
                content = await f.read()
                # 处理空文件的情况
                if content.strip():
                    try:
                        config_data = json.loads(content)
                        print(f"成功读取现有配置，当前任务数量: {len(config_data)}")
                    except json.JSONDecodeError as e:
                        print(f"解析配置文件失败，将创建新配置: {e}")
                        config_data = []
        else:
            print(f"配置文件不存在，将创建新文件: {config_file}")

        # 追加新任务
        config_data.append(new_task)

        # 写回配置文件
        async with aiofiles.open(config_file, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(config_data, ensure_ascii=False, indent=2))
            print(f"配置文件写入完成")

        print(f"成功！新任务 '{new_task.get('task_name')}' 已添加到 {config_file} 并已启用。")
        return True
    except json.JSONDecodeError as e:
        error_msg = f"错误: 配置文件 {config_file} 格式错误，无法解析: {e}"
        sys.stderr.write(error_msg + "\n")
        print(error_msg)
        return False
    except IOError as e:
        error_msg = f"错误: 读写配置文件失败: {e}"
        sys.stderr.write(error_msg + "\n")
        print(error_msg)
        return False
    except Exception as e:
        error_msg = f"错误: 更新配置文件时发生未知错误: {e}"
        sys.stderr.write(error_msg + "\n")
        print(error_msg)
        import traceback
        print(traceback.format_exc())
        return False
