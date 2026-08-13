"""区域归属匹配测试：覆盖真实闲鱼「发货地区」的粒度混乱场景。"""
import pytest

from src.region_resolver import parse_region_target, region_targets_match


@pytest.mark.parametrize(
    "region, expected",
    [
        ("", (None, None, None)),
        (None, (None, None, None)),
        ("全国", (None, None, None)),
        ("海外", (None, None, None)),
        ("广东", ("广东", None, None)),
        ("广东/广州", ("广东", "广州", None)),
        ("广东/广州/全广州", ("广东", "广州", None)),  # 全X 折叠
        ("全广东", (None, None, None)),  # 只有全X -> 空目标
        ("广东/广州/天河区", ("广东", "广州", "天河区")),
        ("北京/朝阳区", ("北京", None, "朝阳区")),  # 直辖市两级
        ("上海/浦东新区", ("上海", None, "浦东新区")),
    ],
)
def test_parse_region_target(region, expected):
    target = parse_region_target(region)
    assert (target.province, target.city, target.district) == expected


@pytest.mark.parametrize(
    "region, area, want",
    [
        # 省市直接命中
        ("广东", "广东", True),
        ("广东", "广州", True),
        ("广东", "天河区", True),
        ("广东", "深圳", True),
        ("广东", "北京", False),
        # 市范围（全X 折叠，严格：省粒度不放行）
        ("广东/广州/全广州", "广州", True),
        ("广东/广州/全广州", "天河区", True),
        ("广东/广州/全广州", "广东", False),  # 省粒度，无法确定是否在广州，不放行
        ("广东/广州/全广州", "深圳", False),
        ("广东/广州/全广州", "福田区", False),
        ("广东/广州/全广州", "东莞", False),
        # 市后缀归一化
        ("广东/东莞/全东莞", "东莞市", True),
        ("广东/东莞/全东莞", "东莞", True),
        # 精确到区
        ("广东/广州/天河区", "天河区", True),
        ("广东/广州/天河区", "荔湾区", False),
        ("广东/广州/天河区", "广州", False),
        # 直辖市
        ("北京/朝阳区", "朝阳区", True),
        ("北京/朝阳区", "海淀区", False),
        ("北京", "朝阳区", True),
        # 县级市归属
        ("山东", "诸城市", True),
        ("山东", "青岛", True),
        ("广东", "诸城市", False),
        # 县归属省
        ("山东/菏泽/曹县", "曹县", True),
        # 未知 area 不应命中
        ("广东", "地区未知", False),
        ("广东", "Shanghai", False),
    ],
)
def test_region_targets_match(region, area, want):
    target = parse_region_target(region)
    assert region_targets_match(area, target) is want


def test_region_trust_province_after_ui_filter():
    # 页面层已成功按区域筛选时，省粒度数据可信任放行
    target = parse_region_target("广东/广州/全广州")
    assert region_targets_match("广东", target) is False
    assert region_targets_match("广东", target, trust_province=True) is True
    assert region_targets_match("深圳", target, trust_province=True) is False
    assert region_targets_match("北京", target, trust_province=True) is False
    # 精确到区时，信任省粒度不应放大到其它区
    district_target = parse_region_target("广东/广州/天河区")
    assert region_targets_match("广东", district_target, trust_province=True) is False
    assert region_targets_match("天河区", district_target, trust_province=True) is True
