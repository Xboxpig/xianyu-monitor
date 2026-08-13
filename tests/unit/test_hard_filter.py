"""
硬过滤层测试：按任务配置对解析后的商品做强制校验（锁死条件）。
先于实现编写：本模块导入 src.hard_filter。
"""
import pytest

from src.hard_filter import apply_hard_filters, parse_price_float


def _item(title="Sony A7M4", price="10000", area="广东 深圳", tags=None):
    return {
        "商品标题": title,
        "当前售价": price,
        "发货地区": area,
        "商品标签": tags if tags is not None else ["包邮"],
    }


# ---------- 价格解析 ----------

def test_parse_price_float_formats():
    assert parse_price_float("299") == 299.0
    assert parse_price_float("¥299") == 299.0
    assert parse_price_float("¥299.5") == 299.5
    assert parse_price_float("0.5万") == 5000.0
    assert parse_price_float("¥1.2万") == 12000.0
    assert parse_price_float("") is None
    assert parse_price_float("价格异常") is None
    assert parse_price_float(None) is None


# ---------- 无配置时全保留（向后兼容） ----------

def test_no_conditions_keeps_all():
    items = [_item(price="100"), _item(price="2000"), _item(price="50000")]
    kept, dropped = apply_hard_filters(items)
    assert len(kept) == 3
    assert dropped == []


# ---------- 价格锁死 ----------

def test_price_range_keeps_in_range():
    items = [_item(price="100"), _item(price="1500"), _item(price="5000")]
    kept, _ = apply_hard_filters(items, min_price="100", max_price="2000")
    assert [i["当前售价"] for i in kept] == ["100", "1500"]


def test_price_min_only():
    items = [_item(price="99"), _item(price="101")]
    kept, _ = apply_hard_filters(items, min_price="100")
    assert [i["当前售价"] for i in kept] == ["101"]


def test_price_max_only():
    items = [_item(price="100"), _item(price="200")]
    kept, _ = apply_hard_filters(items, max_price="150")
    assert [i["当前售价"] for i in kept] == ["100"]


def test_price_wan_format_respected():
    items = [_item(price="0.5万"), _item(price="20000")]
    kept, _ = apply_hard_filters(items, min_price="8000")
    assert [i["当前售价"] for i in kept] == ["20000"]


def test_price_out_of_range_dropped_with_reason():
    item = _item(price="50")
    kept, dropped = apply_hard_filters([item], min_price="100", max_price="200")
    assert kept == []
    assert len(dropped) == 1
    assert "价格" in dropped[0]


def test_unparseable_price_is_dropped_when_price_configured():
    item = _item(price="价格异常")
    kept, dropped = apply_hard_filters([item], min_price="100")
    assert kept == []
    assert "价格" in dropped[0]


# ---------- 区域锁死 ----------

def test_region_province_match_kept():
    # 真实闲鱼 area 是单 token（省名/市名/区名），不是空格拼接
    item = _item(area="深圳")
    kept, _ = apply_hard_filters([item], region="广东/深圳/全深圳")
    assert len(kept) == 1


def test_region_province_mismatch_dropped():
    item = _item(area="杭州")
    kept, dropped = apply_hard_filters([item], region="上海")
    assert kept == []
    assert len(dropped) == 1
    assert "区域" in dropped[0]


def test_region_city_scope_accepts_city_and_districts():
    # 选「广东/广州/全广州」应命中 广州 及其各区，但省粒度「广东」不再放行（严格锁定）
    target_city = "广东/广州/全广州"
    for area in ("广州", "天河区", "荔湾区"):
        kept, _ = apply_hard_filters([_item(area=area)], region=target_city)
        assert len(kept) == 1, f"area={area!r} 应命中 {target_city}"
    for area in ("广东", "深圳", "福田区", "东莞", "东莞市"):
        kept, _ = apply_hard_filters([_item(area=area)], region=target_city)
        assert kept == [], f"area={area!r} 不应命中 {target_city}"


def test_region_district_scope_is_exact():
    # 精确到区时，同市其它区不应命中
    kept, _ = apply_hard_filters([_item(area="天河区")], region="广东/广州/天河区")
    assert len(kept) == 1
    kept, _ = apply_hard_filters([_item(area="荔湾区")], region="广东/广州/天河区")
    assert kept == []


def test_region_city_suffix_normalized():
    # 数据表里市名是裸名（东莞），但闲鱼返回带「市」后缀（东莞市）
    kept, _ = apply_hard_filters([_item(area="东莞市")], region="广东/东莞/全东莞")
    assert len(kept) == 1


def test_region_county_level_city_matches_province():
    # 县级市（诸城市）应能反查到省（山东）
    kept, _ = apply_hard_filters([_item(area="诸城市")], region="山东")
    assert len(kept) == 1
    kept, _ = apply_hard_filters([_item(area="诸城市")], region="广东")
    assert kept == []


def test_region_whole_country_no_filter():
    # 「全国」不锁定区域
    kept, _ = apply_hard_filters([_item(area="上海")], region="全国")
    assert len(kept) == 1


def test_region_ui_applied_trusts_province_granularity():
    # 页面层筛选成功时，省粒度「广东」放行；失败（默认）时严格拦截
    items = [_item(area="广东"), _item(area="深圳")]
    kept, _ = apply_hard_filters(
        items, region="广东/广州/全广州", region_ui_applied=True
    )
    assert [i["发货地区"] for i in kept] == ["广东"]
    kept_strict, _ = apply_hard_filters(
        items, region="广东/广州/全广州", region_ui_applied=False
    )
    assert kept_strict == []


def test_region_missing_area_dropped():
    item = _item(area="地区未知")
    kept, dropped = apply_hard_filters([item], region="广东")
    assert kept == []
    assert len(dropped) == 1
    assert "区域" in dropped[0]


# ---------- 包邮锁死 ----------

def test_free_shipping_keeps_only_tagged_items():
    items = [_item(tags=["包邮"]), _item(tags=["验货宝"])]
    kept, dropped = apply_hard_filters(items, free_shipping=True)
    assert len(kept) == 1
    assert len(dropped) == 1
    assert "包邮" in dropped[0]


def test_free_shipping_disabled_keeps_untagged():
    item = _item(tags=["验货宝"])
    kept, _ = apply_hard_filters([item], free_shipping=False)
    assert len(kept) == 1


def test_free_shipping_missing_tags_field_dropped():
    item = _item(tags=[])
    kept, _ = apply_hard_filters([item], free_shipping=True)
    assert kept == []


# ---------- 关键词锁死 ----------

def test_keyword_mode_any_default_keeps_one_hit():
    items = [_item(title="佳能 R5 相机"), _item(title="索尼 A7M4 相机")]
    kept, _ = apply_hard_filters(items, keyword_rules=["佳能", "索尼"], keyword_rule_mode="any")
    assert len(kept) == 2


def test_keyword_mode_all_requires_every_keyword():
    items = [_item(title="佳能 R5 全画幅"), _item(title="佳能 R5 半画幅")]
    kept, dropped = apply_hard_filters(
        items, keyword_rules=["佳能", "全画幅"], keyword_rule_mode="all"
    )
    assert [i["商品标题"] for i in kept] == ["佳能 R5 全画幅"]
    assert len(dropped) == 1
    assert "关键词" in dropped[0]


def test_exclude_keywords_drop_hits():
    items = [_item(title="全新索尼 A7M4"), _item(title="二手索尼 A7M4")]
    kept, dropped = apply_hard_filters(items, exclude_keywords=["二手"])
    assert [i["商品标题"] for i in kept] == ["全新索尼 A7M4"]
    assert len(dropped) == 1
    assert "排除" in dropped[0]


# ---------- 组合条件 ----------

def test_combined_conditions_all_enforced():
    items = [
        _item(title="佳能全画幅包邮", price="5000", area="深圳", tags=["包邮"]),
        _item(title="佳能全画幅不包邮", price="5000", area="深圳", tags=["验货宝"]),
        _item(title="佳能全画幅包邮贵", price="9000", area="深圳", tags=["包邮"]),
        _item(title="佳能全画幅包邮异地", price="5000", area="杭州", tags=["包邮"]),
    ]
    kept, dropped = apply_hard_filters(
        items,
        min_price="1000",
        max_price="8000",
        region="广东",
        free_shipping=True,
    )
    assert [i["商品标题"] for i in kept] == ["佳能全画幅包邮"]
    assert len(dropped) == 3


def test_dropped_reason_count_matches_dropped_items():
    items = [_item(price="50", area="杭州", tags=[]), _item(price="500", area="广州", tags=["包邮"])]
    kept, dropped = apply_hard_filters(
        items, min_price="100", region="广东", free_shipping=True
    )
    assert len(kept) == 1
    assert len(dropped) == 1
