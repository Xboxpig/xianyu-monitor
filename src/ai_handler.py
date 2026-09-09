import asyncio
import base64
import json
import os
import re
import sys
import shutil
import traceback
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

import requests

# 设置标准输出编码为 UTF-8。不要 detach：pytest、IDE 和 Web 进程可能持有包装流。
if sys.platform.startswith("win"):
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass

from src.config import (
    AI_DEBUG_MODE,
    IMAGE_DOWNLOAD_HEADERS,
    IMAGE_SAVE_DIR,
    TASK_IMAGE_DIR_PREFIX,
    ENABLE_RESPONSE_FORMAT,
)
from src.ai_message_builder import (
    build_analysis_messages,
)
from src.services.ai_response_parser import (
    EmptyAIResponseError,
    parse_ai_response_json,
)
from src.services.ai_retry_control import (
    AIAnalysisRetryCancelled,
    AIAnalysisRetryControl,
)
from src.services.ai_retry_policy import (
    AI_RETRY_MAX_DELAY_SECONDS,
    DEFAULT_AI_RETRY_ATTEMPTS,
    ai_retry_delay_seconds,
)
from src.services.llm_request_queue import (
    LLM_PRIORITY_NORMAL,
    global_llm_request_queue,
)
from src.services.notification_service import build_notification_service
from src.infrastructure.external.ai_client import AIClient
from src.utils import convert_goofish_link, retry_on_failure


def _positive_int(value, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


DEFAULT_IMAGE_DOWNLOAD_CONCURRENCY = max(
    1,
    _positive_int(os.getenv("IMAGE_DOWNLOAD_CONCURRENCY", "3"), 3),
)

DEFAULT_AI_ANALYSIS_MAX_RETRIES = DEFAULT_AI_RETRY_ATTEMPTS
AI_ANALYSIS_RETRY_MAX_DELAY_SECONDS = AI_RETRY_MAX_DELAY_SECONDS


def _analysis_retry_delay_seconds(failed_attempt: int) -> int:
    """Return 1, 2, 4, 8, 16, 32, 32... for zero-based attempts."""
    return ai_retry_delay_seconds(failed_attempt)


async def _wait_before_analysis_retry(
    attempt: int,
    max_retries: int,
    retry_control: AIAnalysisRetryControl | None = None,
    queue_summary: str = "",
    error_reason: str = "",
) -> None:
    delay = _analysis_retry_delay_seconds(attempt)
    safe_print(
        f"   [AI分析] 准备第{attempt + 2}次重试，{delay}秒后继续... "
        f"(最多{max_retries}次)"
    )
    async with global_llm_request_queue.retrying(
        priority=LLM_PRIORITY_NORMAL,
        label="analysis",
        summary=queue_summary,
        retry_attempt=attempt + 2,
        retry_max_attempts=max_retries,
        retry_error=error_reason,
    ):
        if retry_control is None:
            await asyncio.sleep(delay)
            return
        retry_control.update(
            phase="backoff",
            attempt=attempt + 1,
            next_delay_seconds=delay,
        )
        await retry_control.wait(delay)


def safe_print(text):
    """安全的打印函数，处理编码错误"""
    try:
        print(text)
    except UnicodeEncodeError:
        # 如果遇到编码错误，尝试用ASCII编码并忽略无法编码的字符
        try:
            print(text.encode('ascii', errors='ignore').decode('ascii'))
        except:
            # 如果还是失败，打印一个简化的消息
            print("[输出包含无法显示的字符]")


def _build_debug_request_summary(api_mode: str, request_params: dict) -> dict:
    summary = {
        "api_mode": api_mode,
        "model": request_params.get("model"),
    }
    if "temperature" in request_params:
        summary["temperature"] = request_params["temperature"]
    if "reasoning_effort" in request_params:
        summary["reasoning_effort"] = request_params["reasoning_effort"]
    if "reasoning" in request_params:
        summary["reasoning"] = request_params["reasoning"]
    if "max_output_tokens" in request_params:
        summary["max_output_tokens"] = request_params["max_output_tokens"]
    if "max_tokens" in request_params:
        summary["max_tokens"] = request_params["max_tokens"]
    if "text" in request_params:
        summary["text"] = request_params["text"]
    if "response_format" in request_params:
        summary["response_format"] = request_params["response_format"]
    if "input" in request_params:
        summary["input_content_types"] = [
            [item.get("type") for item in message.get("content", [])]
            for message in request_params["input"]
        ]
    if "messages" in request_params:
        summary["message_content_types"] = [
            _extract_message_content_types(message)
            for message in request_params["messages"]
        ]
    return summary


def _extract_message_content_types(message: dict) -> list[str]:
    content = message.get("content")
    if isinstance(content, str):
        return ["text"]
    if not isinstance(content, list):
        return [type(content).__name__]
    return [str(item.get("type")) for item in content if isinstance(item, dict)]


@retry_on_failure(retries=2, delay=3)
async def _download_single_image(url, save_path):
    """一个带重试的内部函数，用于异步下载单个图片。"""
    loop = asyncio.get_running_loop()
    # 使用 run_in_executor 运行同步的 requests 代码，避免阻塞事件循环
    response = await loop.run_in_executor(
        None,
        lambda: requests.get(url, headers=IMAGE_DOWNLOAD_HEADERS, timeout=20, stream=True)
    )
    response.raise_for_status()
    with open(save_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    return save_path


def _build_image_save_path(
    product_id: str,
    index: int,
    url: str,
    task_image_dir: str,
) -> str:
    clean_url = url.split('.heic')[0] if '.heic' in url else url
    file_name_base = os.path.basename(clean_url).split('?')[0]
    file_name = f"product_{product_id}_{index}_{file_name_base}"
    file_name = re.sub(r'[\\/*?:"<>|]', "", file_name)
    if not os.path.splitext(file_name)[1]:
        file_name += ".jpg"
    return os.path.join(task_image_dir, file_name)


async def download_all_images(product_id, image_urls, task_name="default", concurrency=None):
    """异步下载一个商品的所有图片。如果图片已存在则跳过。支持任务隔离。"""
    if not image_urls:
        return []

    # 为每个任务创建独立的图片目录
    task_image_dir = os.path.join(IMAGE_SAVE_DIR, f"{TASK_IMAGE_DIR_PREFIX}{task_name}")
    os.makedirs(task_image_dir, exist_ok=True)

    urls = [url.strip() for url in image_urls if url.strip().startswith('http')]
    if not urls:
        return []

    max_concurrency = _positive_int(concurrency, DEFAULT_IMAGE_DOWNLOAD_CONCURRENCY)
    semaphore = asyncio.Semaphore(max_concurrency)
    total_images = len(urls)

    async def _download_one(index: int, url: str):
        save_path = _build_image_save_path(product_id, index, url, task_image_dir)
        if os.path.exists(save_path):
            safe_print(
                f"   [图片] 图片 {index}/{total_images} 已存在，跳过下载: {os.path.basename(save_path)}"
            )
            return save_path
        async with semaphore:
            safe_print(f"   [图片] 正在下载图片 {index}/{total_images}: {url}")
            if await _download_single_image(url, save_path):
                safe_print(
                    f"   [图片] 图片 {index}/{total_images} 已成功下载到: {os.path.basename(save_path)}"
                )
                return save_path
        return None

    tasks = [
        asyncio.create_task(_download_one(index, url))
        for index, url in enumerate(urls, start=1)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    saved_paths = []
    for url, result in zip(urls, results):
        try:
            if isinstance(result, Exception):
                raise result
            if result:
                saved_paths.append(result)
        except Exception as e:
            safe_print(f"   [图片] 处理图片 {url} 时发生错误，已跳过此图: {e}")

    return saved_paths


def cleanup_task_images(task_name):
    """清理指定任务的图片目录"""
    task_image_dir = os.path.join(IMAGE_SAVE_DIR, f"{TASK_IMAGE_DIR_PREFIX}{task_name}")
    if os.path.exists(task_image_dir):
        try:
            shutil.rmtree(task_image_dir)
            safe_print(f"   [清理] 已删除任务 '{task_name}' 的临时图片目录: {task_image_dir}")
        except Exception as e:
            safe_print(f"   [清理] 删除任务 '{task_name}' 的临时图片目录时出错: {e}")
    else:
        safe_print(f"   [清理] 任务 '{task_name}' 的临时图片目录不存在: {task_image_dir}")


def cleanup_ai_logs(logs_dir: str, keep_days: int = 1) -> None:
    try:
        cutoff = datetime.now() - timedelta(days=keep_days)
        for filename in os.listdir(logs_dir):
            if not filename.endswith(".log"):
                continue
            try:
                timestamp = datetime.strptime(filename[:15], "%Y%m%d_%H%M%S")
            except ValueError:
                continue
            if timestamp < cutoff:
                os.remove(os.path.join(logs_dir, filename))
    except Exception as e:
        safe_print(f"   [日志] 清理AI日志时出错: {e}")


def encode_image_to_base64(image_path):
    """将本地图片文件编码为 Base64 字符串。"""
    if not image_path or not os.path.exists(image_path):
        return None
    try:
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    except Exception as e:
        safe_print(f"编码图片时出错: {e}")
        return None


def validate_ai_response_format(parsed_response):
    """验证AI响应的格式是否符合预期结构"""
    required_fields = [
        "prompt_version",
        "is_recommended",
        "reason",
        "risk_tags",
        "criteria_analysis"
    ]

    # 检查顶层字段
    for field in required_fields:
        if field not in parsed_response:
            safe_print(f"   [AI分析] 警告：响应缺少必需字段 '{field}'")
            return False

    # 检查criteria_analysis是否为字典且不为空
    criteria_analysis = parsed_response.get("criteria_analysis", {})
    if not isinstance(criteria_analysis, dict) or not criteria_analysis:
        safe_print("   [AI分析] 警告：criteria_analysis必须是非空字典")
        return False

    # 检查seller_type字段（所有商品都需要）
    if "seller_type" not in criteria_analysis:
        safe_print("   [AI分析] 警告：criteria_analysis缺少必需字段 'seller_type'")
        return False

    # 检查数据类型
    if not isinstance(parsed_response.get("is_recommended"), bool):
        safe_print("   [AI分析] 警告：is_recommended字段不是布尔类型")
        return False

    if not isinstance(parsed_response.get("risk_tags"), list):
        safe_print("   [AI分析] 警告：risk_tags字段不是列表类型")
        return False

    return True


@retry_on_failure(retries=3, delay=5)
async def send_ntfy_notification(product_data, reason):
    """兼容旧调用名，内部统一走 NotificationService。"""
    service = build_notification_service()
    if not service.clients:
        safe_print(
            "警告：未在 .env 文件中配置任何通知服务，跳过通知。"
        )
        return {}

    results = await service.send_notification(product_data, reason)
    for channel, result in results.items():
        if result["success"]:
            safe_print(f"   -> {channel} 通知发送成功。")
            continue
        safe_print(f"   -> {channel} 通知发送失败: {result['message']}")
    return results


async def get_ai_analysis(
    product_data,
    image_paths=None,
    prompt_text="",
    task_id: int | None = None,
):
    """将完整的商品JSON数据和所有图片发送给 AI 进行分析（异步）。"""
    item_info = product_data.get('商品信息', {})
    product_id = item_info.get('商品ID', 'N/A')
    product_title = str(item_info.get('商品标题', '无') or '无')
    task_name = str(
        product_data.get("任务名称") or product_data.get("任务名") or "未知任务"
    )
    queue_summary = f"{task_name} - {product_title}"

    safe_print(f"\n   [AI分析] 开始分析商品 #{product_id} (含 {len(image_paths or [])} 张图片)...")
    safe_print(f"   [AI分析] 标题: {item_info.get('商品标题', '无')}")

    if not prompt_text:
        safe_print("   [AI分析] 错误：未提供AI分析所需的prompt文本。")
        return None

    product_details_json = json.dumps(product_data, ensure_ascii=False, indent=2)
    if AI_DEBUG_MODE:
        safe_print("\n--- [AI DEBUG] ---")
        safe_print("--- PRODUCT DATA (JSON) ---")
        safe_print(product_details_json)
        safe_print("--- PROMPT TEXT (完整内容) ---")
        safe_print(prompt_text)
        safe_print("-------------------\n")

    image_data_urls = []
    if image_paths:
        for path in image_paths:
            base64_image = encode_image_to_base64(path)
            if base64_image:
                image_data_urls.append(f"data:image/jpeg;base64,{base64_image}")

    messages = build_analysis_messages(
        product_details_json,
        prompt_text,
        image_data_urls,
    )

    # 保存最终传输内容到日志文件
    try:
        # 创建logs文件夹
        logs_dir = os.path.join("logs", "ai")
        os.makedirs(logs_dir, exist_ok=True)
        cleanup_ai_logs(logs_dir, keep_days=1)

        # 生成日志文件名（当前时间）
        current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_filename = f"{current_time}.log"
        log_filepath = os.path.join(logs_dir, log_filename)

        task_name = product_data.get("任务名称") or product_data.get("任务名") or "unknown"
        log_payload = {
            "timestamp": current_time,
            "task_name": task_name,
            "product_id": product_id,
            "title": item_info.get("商品标题", "无"),
            "image_count": len(image_data_urls),
        }
        log_content = json.dumps(log_payload, ensure_ascii=False)

        # 写入日志文件
        with open(log_filepath, 'w', encoding='utf-8') as f:
            f.write(log_content)

        safe_print(f"   [日志] AI分析请求已保存到: {log_filepath}")

    except Exception as e:
        safe_print(f"   [日志] 保存AI分析日志时出错: {e}")

    retry_control = AIAnalysisRetryControl.from_runtime(
        product_id=str(product_id),
        max_attempts=DEFAULT_AI_ANALYSIS_MAX_RETRIES,
        task_id=task_id,
    )
    try:
        return await _run_ai_analysis_attempts(
            messages,
            retry_control,
            queue_summary=queue_summary,
        )
    finally:
        retry_control.close()


async def _run_ai_analysis_attempts(
    messages: list[dict],
    retry_control: AIAnalysisRetryControl,
    queue_summary: str = "",
):
    """Run validated AI analysis attempts with task-scoped cancellation."""
    max_retries = DEFAULT_AI_ANALYSIS_MAX_RETRIES
    previous_error = ""
    for attempt in range(max_retries):
        try:
            retry_control.update(
                phase="request",
                attempt=attempt + 1,
                next_delay_seconds=None,
            )
            retry_control.raise_if_cancelled()
            # 根据重试次数调整参数
            current_temperature = 0.1 if attempt == 0 else 0.05  # 重试时使用更低的温度

            ai_client = AIClient()
            if not ai_client.is_available():
                raise RuntimeError("AI 客户端未初始化")
            try:
                ai_response_content = await retry_control.run(
                    ai_client._call_ai(
                        messages,
                        temperature=current_temperature,
                        max_output_tokens=4000,
                        enable_json_output=ENABLE_RESPONSE_FORMAT,
                        request_label="analysis",
                        request_summary=queue_summary,
                        retry_attempt=attempt + 1 if attempt > 0 else None,
                        retry_max_attempts=max_retries if attempt > 0 else None,
                        retry_error=previous_error,
                    )
                )
                if AI_DEBUG_MODE:
                    safe_print(
                        "--- [AI DEBUG] TRANSPORT ---\n"
                        + json.dumps(
                            ai_client.last_resolution or {},
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
            finally:
                await ai_client.close()

            if AI_DEBUG_MODE:
                safe_print(f"\n--- [AI DEBUG] 第{attempt + 1}次尝试 ---")
                safe_print("--- RAW AI RESPONSE ---")
                safe_print(ai_response_content)
                safe_print("---------------------\n")

            try:
                parsed_response = parse_ai_response_json(ai_response_content)

                # 验证响应格式
                if validate_ai_response_format(parsed_response):
                    safe_print(f"   [AI分析] 第{attempt + 1}次尝试成功，响应格式验证通过")
                    return parsed_response
                safe_print(f"   [AI分析] 第{attempt + 1}次尝试格式验证失败")
                if attempt < max_retries - 1:
                    previous_error = "AI响应格式缺少必需字段或字段类型不正确。"
                    await _wait_before_analysis_retry(
                        attempt,
                        max_retries,
                        retry_control,
                        queue_summary,
                        previous_error,
                    )
                    continue
                raise ValueError("AI响应格式缺少必需字段或字段类型不正确。")
            except json.JSONDecodeError as e:
                safe_print(f"   [AI分析] 第{attempt + 1}次尝试JSON解析失败: {e}")
                if attempt < max_retries - 1:
                    previous_error = str(e)
                    await _wait_before_analysis_retry(
                        attempt,
                        max_retries,
                        retry_control,
                        queue_summary,
                        previous_error,
                    )
                    continue
                raise e
            except EmptyAIResponseError as e:
                safe_print(f"   [AI分析] 第{attempt + 1}次尝试返回空响应: {e}")
                if attempt < max_retries - 1:
                    previous_error = str(e)
                    await _wait_before_analysis_retry(
                        attempt,
                        max_retries,
                        retry_control,
                        queue_summary,
                        previous_error,
                    )
                    continue
                raise e

        except Exception as e:
            if AI_DEBUG_MODE:
                safe_print(f"\n--- [AI DEBUG] 第{attempt + 1}次尝试 EXCEPTION ---")
                safe_print(repr(e))
                safe_print(traceback.format_exc())
                safe_print("-------------------------------------\n")
            safe_print(f"   [AI分析] 第{attempt + 1}次尝试AI调用失败: {e}")
            if isinstance(e, (EmptyAIResponseError, AIAnalysisRetryCancelled)):
                raise
            if attempt < max_retries - 1:
                previous_error = str(e)
                await _wait_before_analysis_retry(
                    attempt,
                    max_retries,
                    retry_control,
                    queue_summary,
                    previous_error,
                )
                continue
            raise e
