"""
硬过滤层：按任务配置对解析后的商品做强制校验（锁死条件）。

价格、区域、包邮、关键词等条件在页面上通过 UI 点击筛选，
UI 失败时不会拦截商品，本层在解析后兜底强制过滤。
"""
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.keyword_rule_engine import (
    _keyword_matches,
    _normalize_keywords,
    normalize_text,
)
from src.region_resolver import parse_region_target, region_targets_match

_PRICE_NUMBER_PATTERN = re.compile(r"(\d+(?:\.\d+)?)")


def parse_price_float(price: Any) -> Optional[float]:
    """解析商品价格字符串为浮点数，支持 ¥ 前缀与“万”单位。解析失败返回 None。"""
    if price is None:
        return None
    text = str(price).strip()
    if not text:
        return None
    cleaned = text.replace("¥", "").replace("￥", "").replace(",", "")
    match = _PRICE_NUMBER_PATTERN.search(cleaned)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    if "万" in cleaned:
        value *= 10000
    return value


def _item_search_text(item: Dict[str, Any]) -> str:
    parts: List[str] = []
    for value in item.values():
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(x) for x in value if x is not None)
    return normalize_text(" ".join(parts))


def _collect_drop_reasons(
    item: Dict[str, Any],
    *,
    min_price: Optional[str],
    max_price: Optional[str],
    region_target,
    region_ui_applied: bool,
    free_shipping: bool,
    keyword_rules: Sequence[str],
    keyword_rule_mode: str,
    exclude_keywords: Sequence[str],
) -> List[str]:
    reasons: List[str] = []

    min_float = parse_price_float(min_price) if min_price else None
    max_float = parse_price_float(max_price) if max_price else None
    if min_float is not None or max_float is not None:
        price_value = parse_price_float(item.get("当前售价"))
        if price_value is None:
            reasons.append(
                f"价格 {item.get('当前售价')} 无法解析，超出范围 {min_price or '不限'}-{max_price or '不限'}"
            )
        elif (min_float is not None and price_value < min_float) or (
            max_float is not None and price_value > max_float
        ):
            reasons.append(
                f"价格 {item.get('当前售价')} 超出范围 {min_price or '不限'}-{max_price or '不限'}"
            )

    if not region_target.is_empty():
        area = str(item.get("发货地区", "") or "")
        if not region_targets_match(area, region_target, trust_province=region_ui_applied):
            reasons.append(f"区域 {area} 不匹配 {region_target}")

    if free_shipping:
        tags = item.get("商品标签") or []
        if "包邮" not in tags:
            reasons.append("未包含包邮标签")

    normalized_excludes = _normalize_keywords(exclude_keywords)
    if normalized_excludes:
        text = _item_search_text(item)
        hit_excludes = [
            kw for kw in normalized_excludes if _keyword_matches(kw, text)
        ]
        if hit_excludes:
            reasons.append(f"命中排除词: {', '.join(hit_excludes)}")

    normalized_rules = _normalize_keywords(keyword_rules)
    if normalized_rules:
        text = _item_search_text(item)
        if keyword_rule_mode == "all":
            missed = [
                kw for kw in normalized_rules if not _keyword_matches(kw, text)
            ]
            if missed:
                reasons.append(f"未命中全部关键词, 缺少: {', '.join(missed)}")
        else:
            hits = [kw for kw in normalized_rules if _keyword_matches(kw, text)]
            if not hits:
                reasons.append("未命中任何关键词")

    return reasons


def apply_hard_filters(
    items: Sequence[Dict[str, Any]],
    *,
    min_price: Optional[str] = None,
    max_price: Optional[str] = None,
    region: Optional[str] = None,
    region_ui_applied: bool = False,
    free_shipping: bool = False,
    keyword_rules: Sequence[str] = (),
    keyword_rule_mode: str = "any",
    exclude_keywords: Sequence[str] = (),
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    按任务条件强制过滤商品列表。

    region_ui_applied: 页面层的区域筛选（弹窗点选提交）是否成功。
    成功时，页面已把结果约束到目标区域，省粒度数据（如「广东」）可信任放行；
    失败时，省粒度数据无法确认归属，按严格模式拦截。

    返回 (保留的商品列表, 丢弃原因列表)，与传入顺序一一对应。
    """
    region_target = parse_region_target(region)

    kept: List[Dict[str, Any]] = []
    dropped: List[str] = []
    for item in items:
        reasons = _collect_drop_reasons(
            item,
            min_price=min_price,
            max_price=max_price,
            region_target=region_target,
            region_ui_applied=region_ui_applied,
            free_shipping=free_shipping,
            keyword_rules=keyword_rules,
            keyword_rule_mode=keyword_rule_mode,
            exclude_keywords=exclude_keywords,
        )
        if reasons:
            dropped.append("、".join(reasons))
        else:
            kept.append(item)
    return kept, dropped
