import asyncio

from src.services.result_storage_service import (
    load_ai_recovery_records,
    save_result_record,
    update_result_ai_analysis,
)


def _record(item_id: str, analysis):
    record = {
        "爬取时间": "2026-09-06T10:00:00",
        "搜索关键字": "demo",
        "任务名称": "task-a",
        "商品信息": {
            "商品ID": item_id,
            "商品标题": item_id,
            "商品链接": f"https://example.com/{item_id}",
        },
    }
    if analysis is not None:
        record["ai_analysis"] = analysis
    return record


def test_recovery_records_include_only_missing_or_failed_ai(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DATABASE_FILE", str(tmp_path / "app.sqlite3"))

    async def scenario():
        await save_result_record(_record("missing", None), "demo")
        await save_result_record(
            _record(
                "failed",
                {
                    "analysis_source": "ai",
                    "is_recommended": False,
                    "reason": "AI分析异常",
                    "error": "response.failed",
                },
            ),
            "demo",
        )
        await save_result_record(
            _record(
                "normal",
                {
                    "analysis_source": "ai",
                    "is_recommended": False,
                    "reason": "不符合筛选条件",
                },
            ),
            "demo",
        )
        await save_result_record(
            _record(
                "keyword",
                {
                    "analysis_source": "keyword",
                    "is_recommended": True,
                    "reason": "关键词命中",
                },
            ),
            "demo",
        )

        candidates = await load_ai_recovery_records("task-a")
        assert [entry["record"]["商品信息"]["商品ID"] for entry in candidates] == [
            "missing",
            "failed",
        ]

        assert await update_result_ai_analysis(
            candidates[0]["row_id"],
            {
                "analysis_source": "ai",
                "is_recommended": True,
                "reason": "补分析成功",
                "keyword_hit_count": 0,
            },
        )
        remaining = await load_ai_recovery_records("task-a")
        assert [entry["record"]["商品信息"]["商品ID"] for entry in remaining] == [
            "failed"
        ]

    asyncio.run(scenario())
