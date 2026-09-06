"""
AI 请求消息构造辅助函数
"""
from typing import Dict, List, Union


TEXT_ONLY_ANALYSIS_NOTE = (
    "补充说明：本次未提供商品图片，请仅根据商品文字字段和卖家信息判断，不要推断图片内容。"
)
IMAGE_ANALYSIS_NOTE = (
    "如本次未提供商品图片，请仅根据商品文字字段和卖家信息判断，不要推断图片内容。"
)
VALUE_ANALYSIS_NOTE = (
    "如果商品 JSON 中包含“价格参考”或 price_insight，请结合价格位置、历史走势、"
    "配置、成色、附件、卖家信息综合判断性价比。"
    "你可以额外输出可选字段 value_score(0-100) 和 value_summary，"
    "但必须保留原有 is_recommended/reason 等字段。"
)


def build_analysis_system_prompt(prompt_text: str) -> str:
    """构建所有商品共享的稳定 system prompt，便于 Prompt Caching 复用。"""
    return f"{prompt_text.rstrip()}\n\n{VALUE_ANALYSIS_NOTE}\n{IMAGE_ANALYSIS_NOTE}"


def build_product_text_prompt(product_json: str) -> str:
    """构建每件商品独有的文本内容。"""
    return f"""请分析以下完整的商品 JSON 数据：

```json
{product_json}
```"""


def build_analysis_text_prompt(
    product_json: str,
    prompt_text: str,
    *,
    include_images: bool,
) -> str:
    """兼容旧调用：返回组合后的纯文本 Prompt。"""
    note = "" if include_images else f"\n{TEXT_ONLY_ANALYSIS_NOTE}\n"
    return f"""请基于你的专业知识和我的要求，分析以下完整的商品JSON数据：

```json
{product_json}
```

    {prompt_text}
    {VALUE_ANALYSIS_NOTE}
    {note}"""


def build_user_message_content(
    text_prompt: str,
    image_data_urls: List[str],
) -> Union[str, List[Dict[str, object]]]:
    if not image_data_urls:
        return text_prompt

    user_content: List[Dict[str, object]] = [{"type": "text", "text": text_prompt}]
    user_content.extend(
        {"type": "image_url", "image_url": {"url": url}}
        for url in image_data_urls
    )
    return user_content


def build_analysis_messages(
    product_json: str,
    prompt_text: str,
    image_data_urls: List[str],
) -> List[Dict[str, object]]:
    """按稳定 Prompt、动态商品 JSON、动态图片的顺序构建消息。"""
    return [
        {"role": "system", "content": build_analysis_system_prompt(prompt_text)},
        {
            "role": "user",
            "content": build_user_message_content(
                build_product_text_prompt(product_json),
                image_data_urls,
            ),
        },
    ]
