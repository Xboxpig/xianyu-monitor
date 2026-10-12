"""Apply the existing filters, analysis and storage to extension-collected items."""
import asyncio
import contextlib
import os
from datetime import datetime

from src.ai_handler import download_all_images, get_ai_analysis, send_ntfy_notification
from src.domain.models.browser import BrowserItem
from src.domain.models.task import Task
from src.hard_filter import apply_hard_filters
from src.keyword_rule_engine import build_search_text, evaluate_keyword_rules
from src.services.price_history_service import build_market_reference, load_price_snapshots, record_market_snapshots
from src.services.result_storage_service import save_result_record


class NativeResultProcessor:
    def load_prompt(self, task: Task) -> str:
        if task.decision_mode != "ai":
            return ""
        with open(task.ai_prompt_base_file, encoding="utf-8") as source:
            base = source.read()
        with open(task.ai_prompt_criteria_file, encoding="utf-8") as source:
            criteria = source.read()
        return base.replace("{{CRITERIA_SECTION}}", criteria)

    def filter(self, task: Task, items: list[BrowserItem], region_applied=False, *, check_personal=True):
        eligible = [
            item for item in items
            if not check_personal or not task.personal_only or item.personal is True
        ]
        records = [item.to_record_item() for item in eligible]
        kept, reasons = apply_hard_filters(
            records, min_price=task.min_price, max_price=task.max_price,
            region=task.region, region_ui_applied=region_applied,
            free_shipping=task.free_shipping, keyword_rules=task.keyword_rules,
            keyword_rule_mode=task.keyword_rule_mode, exclude_keywords=task.exclude_keywords,
        )
        ids = {item["商品ID"] for item in kept}
        if len(eligible) != len(items):
            reasons.append("卖家个人身份未确认，已按仅个人卖家条件过滤")
        return [item for item in eligible if item.id in ids], reasons

    async def record_market(self, task: Task, items: list[BrowserItem], run_id: str):
        await asyncio.to_thread(
            record_market_snapshots, keyword=task.keyword, task_name=task.task_name,
            items=[item.to_record_item() for item in items], run_id=run_id,
        )

    async def process(self, task: Task, item: BrowserItem, seller: dict, prompt: str,
                      market: list[BrowserItem], log) -> None:
        info = item.to_record_item()
        reference = await asyncio.to_thread(
            build_market_reference, keyword=task.keyword, item=info,
            current_market_items=[entry.to_record_item() for entry in market],
            historical_snapshots=await asyncio.to_thread(load_price_snapshots, task.keyword),
        )
        record = {
            "爬取时间": datetime.now().isoformat(), "搜索关键字": task.keyword,
            "任务名称": task.task_name, "商品信息": info,
            "卖家信息": {
                **seller, "卖家昵称": seller.get("卖家昵称") or item.seller_nickname,
                "卖家芝麻信用": item.seller_credit,
                "卖家注册时长": item.seller_registration,
            },
            "价格参考": reference, "price_insight": reference.get("本商品价格位置", {}),
        }
        images = []
        try:
            if task.decision_mode == "keyword":
                analysis = evaluate_keyword_rules(task.keyword_rules, build_search_text(record))
            else:
                if task.analyze_images:
                    images = await download_all_images(item.id, item.images, task.task_name)
                log("[AI分析] 开始分析商品 " + item.id)
                analysis = await get_ai_analysis(record, image_paths=images, prompt_text=prompt, task_id=task.id)
                if not analysis:
                    raise RuntimeError("AI analysis returned no result after retries.")
                analysis.setdefault("analysis_source", "ai")
                analysis.setdefault("keyword_hit_count", 0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            analysis = {"analysis_source": "ai", "is_recommended": False,
                        "reason": str(exc), "error": str(exc), "keyword_hit_count": 0}
            log("[AI分析] 失败，结果已保留供补分析：" + str(exc))
        finally:
            for path in images:
                with contextlib.suppress(OSError):
                    os.remove(path)
        record["ai_analysis"] = analysis
        await save_result_record(record, task.keyword)
        log("[结果] 已保存商品 " + item.id)
        if analysis.get("is_recommended"):
            try:
                await send_ntfy_notification(info, analysis.get("reason", ""))
            except Exception as exc:
                log("[通知] 发送失败：" + str(exc))
