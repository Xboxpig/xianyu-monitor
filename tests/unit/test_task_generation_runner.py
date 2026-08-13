"""任务生成执行器测试：需求回流（结构化搜索参数回填）。"""
import pytest

from src.domain.models.task import TaskGenerateRequest
from src.services.task_generation_runner import build_task_create


def _req(**overrides) -> TaskGenerateRequest:
    base = dict(
        task_name="测试任务",
        keyword="gtx1060",
        description="需要一张 gtx1060 3g 显卡，预算 300-600，不要矿卡",
        analyze_images=True,
        personal_only=True,
        min_price=None,
        max_price=None,
        max_pages=3,
        cron=None,
        account_state_file=None,
        account_strategy="auto",
        free_shipping=True,
        new_publish_option=None,
        region=None,
        decision_mode="ai",
        keyword_rules=[],
        keyword_rule_mode="any",
        exclude_keywords=[],
    )
    base.update(overrides)
    return TaskGenerateRequest(**base)


def test_backfill_price_range_when_user_empty():
    task = build_task_create(
        _req(),
        "prompts/test_criteria.txt",
        {"min_price": 300, "max_price": 600, "exclude_keywords": ["矿卡"]},
    )
    assert task.min_price == "300"
    assert task.max_price == "600"


def test_user_price_wins_over_extracted():
    task = build_task_create(
        _req(min_price="200", max_price="800"),
        "prompts/test_criteria.txt",
        {"min_price": 300, "max_price": 600},
    )
    assert task.min_price == "200"
    assert task.max_price == "800"


def test_float_price_is_formatted():
    task = build_task_create(
        _req(),
        "prompts/test_criteria.txt",
        {"min_price": 299.5, "max_price": 600.0},
    )
    assert task.min_price == "299.5"
    assert task.max_price == "600"


def test_exclude_keywords_backfilled_when_user_empty():
    task = build_task_create(
        _req(),
        "prompts/test_criteria.txt",
        {"exclude_keywords": ["矿卡", "二手"]},
    )
    assert task.exclude_keywords == ["矿卡", "二手"]


def test_user_exclude_keywords_win():
    task = build_task_create(
        _req(exclude_keywords=["自用"]),
        "prompts/test_criteria.txt",
        {"exclude_keywords": ["矿卡"]},
    )
    assert task.exclude_keywords == ["自用"]


def test_no_extracted_params_keeps_defaults():
    task = build_task_create(_req(), "prompts/test_criteria.txt", {})
    assert task.min_price is None
    assert task.max_price is None
    assert task.exclude_keywords == []
