"""
区域目标解析与宽松归属匹配。

闲鱼商品「发货地区」字段（main_data.area）粒度不固定且通常不含省名：
既可能是省（"广东"、"北京"），也可能是市（"东莞市"、"诸城市"），
还可能是区/县（"天河区"、"曹县"），甚至带「市/区」后缀或不带。

因此不能像早期实现那样「取 region 第一段当省，再对 area 做子串包含」，
而是把 region 解析成明确的省市目标，再通过正向归属判断 area 是否落入目标。

匹配采用「正向归属」而非「反向反查」，避免同名区（如全国多处「朝阳区」）歧义。
"""
from __future__ import annotations

from typing import NamedTuple, Optional, Set

from src.region_data import (
    CITY_DISTRICTS,
    MUNICIPALITIES,
    PROVINCE_ALL_NAMES,
)


class RegionTarget(NamedTuple):
    province: Optional[str]
    city: Optional[str]
    district: Optional[str]

    def is_empty(self) -> bool:
        return self.province is None and self.city is None and self.district is None

    def __str__(self) -> str:
        parts = [p for p in (self.province, self.city, self.district) if p]
        return "/".join(parts) if parts else "(未指定)"


_SUFFIXES = ("特别行政区", "自治州", "自治县", "自治旗", "市", "地区", "区", "县", "州", "盟", "旗")


def _strip_admin_suffix(value: str) -> str:
    """剥离行政区后缀（市/区/县/州等），让「东莞市」与「东莞」等价。"""
    text = (value or "").strip()
    for suffix in _SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[: -len(suffix)]
    return text


def _equivalents(value: str) -> Set[str]:
    """返回一个名称的等价集合：原始、剥离「全」前缀、剥离行政后缀。"""
    text = (value or "").strip()
    result = {text}
    if text.startswith("全") and len(text) > 1:
        result.add(text[1:])
    stripped = _strip_admin_suffix(text)
    if stripped:
        result.add(stripped)
    return result


def _name_in(name: str, bucket: Set[str]) -> bool:
    """判断 name 是否存在于 bucket 中（对 name 做后缀/全X 等价扩展后逐一比对）。"""
    for cand in _equivalents(name):
        if cand in bucket:
            return True
    return False


def parse_region_target(region: Optional[str]) -> RegionTarget:
    """把用户选择的区域字符串（如 "广东/广州/全广州"）解析为省/市/区目标。

    约定：
    - 「全国」「海外」和空值视为无目标（不过滤）。
    - 「全X」占位符表示该级别「全境」，会被折叠掉而不是当作具体行政区：
      "广东/广州/全广州" -> 广州全市（district=None）；"全广东" -> 不过滤。
    - 直辖市（北京/天津/上海/重庆）只有两级：region 形如 "北京/朝阳区"，
      此时 province=北京、city=朝阳区（区作为"市"级处理）。
    """
    if not region:
        return RegionTarget(None, None, None)

    parts = [p.strip() for p in str(region).split("/") if p.strip()]
    if not parts:
        return RegionTarget(None, None, None)

    # 「全国/海外」不锁定具体区域
    if parts[0] in ("全国", "海外"):
        return RegionTarget(None, None, None)

    # 折叠「全X」占位：表示「全境」，不表示具体一级行政区。
    cleaned = [p for p in parts if not (p.startswith("全") and len(p) > 1)]

    province = cleaned[0] if len(cleaned) >= 1 else None
    city = cleaned[1] if len(cleaned) >= 2 else None
    district = cleaned[2] if len(cleaned) >= 3 else None

    # 直辖市没有真正的市一级，把第二段（区）压到 city 位
    if province in MUNICIPALITIES:
        if city is not None:
            district = city
            city = None

    return RegionTarget(province or None, city or None, district or None)


def region_targets_match(
    area: str,
    target: RegionTarget,
    *,
    trust_province: bool = False,
) -> bool:
    """判断商品「发货地区」是否落入目标区域（宽松正向归属）。

    命中规则：
    - 目标为空（全国/海外/空）=> True。
    - 指定到区 => 仅 area 精确等于该区（含后缀等价）。
    - 指定到市 => area == 该市，或属于该市下某区；
      当 trust_province=True（页面层已成功按区域筛选）时，省粒度数据（如「广东」）
      可信任放行；否则省粒度数据不放行（严格锁定）。
    - 指定到省 => area == 该省，或属于该省下某市/区。
    """
    if target.is_empty():
        return True

    area_cands = _equivalents(area or "")

    if target.district:
        return bool(area_cands & _equivalents(target.district))

    if target.city:
        city_name = target.city
        if bool(area_cands & _equivalents(city_name)):
            return True
        # area 属于该市下某区
        for district_name in CITY_DISTRICTS.get(city_name, []):
            if bool(area_cands & _equivalents(district_name)):
                return True
        # 页面层已成功按区域筛选时，省粒度数据可信任放行
        if trust_province and target.province and bool(
            area_cands & _equivalents(target.province)
        ):
            return True
        return False

    # 指定到省
    prov = target.province
    if not prov:
        return True
    if bool(area_cands & _equivalents(prov)):
        return True
    for name in PROVINCE_ALL_NAMES.get(prov, set()):
        if bool(area_cands & _equivalents(name)):
            return True
    return False
