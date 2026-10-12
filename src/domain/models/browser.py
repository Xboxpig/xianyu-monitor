"""Messages exchanged with the native Chrome extension."""
from typing import Literal
from pydantic import BaseModel, Field, field_validator
from urllib.parse import urlparse


class BrowserMemory(BaseModel):
    run_id: str = Field(min_length=1, max_length=100)
    slot_id: str = Field(min_length=1, max_length=100)
    memory_mb: int = Field(ge=0, le=1_048_576)


class BrowserClient(BaseModel):
    client_id: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(default="Chrome", max_length=100)
    account_state_file: str | None = Field(default=None, max_length=300)
    profile_directory: str | None = Field(default=None, max_length=100)
    version: str = Field(default="", max_length=30)
    parallel: bool = False
    windows: list[BrowserMemory] = Field(default_factory=list, max_length=4096)


class BrowserItem(BaseModel):
    id: str = Field(min_length=1, max_length=30, pattern=r"^\d+$")
    title: str = Field(default="", max_length=2000)
    description: str = Field(default="", max_length=30000)
    price: str = Field(default="", max_length=50)
    original_price: str = Field(default="", max_length=50)
    area: str = Field(default="", max_length=200)
    seller_nickname: str = Field(default="", max_length=200)
    seller_id: str = Field(default="", max_length=30, pattern=r"^\d*$")
    images: list[str] = Field(default_factory=list, max_length=30)
    tags: list[str] = Field(default_factory=list, max_length=30)
    publish_time: str = Field(default="", max_length=100)
    wants: str = Field(default="", max_length=50)
    views: str = Field(default="", max_length=50)
    personal: bool | None = None
    seller_credit: str = Field(default="", max_length=200)
    seller_registration: str = Field(default="", max_length=200)

    @field_validator("images")
    @classmethod
    def validate_images(cls, values):
        for value in values:
            parsed = urlparse(value)
            host = (parsed.hostname or "").lower()
            if (parsed.scheme != "https" or parsed.username or parsed.password
                    or parsed.port not in (None, 443)
                    or not any(host == domain or host.endswith("." + domain)
                               for domain in ("alicdn.com", "goofish.com", "taobaocdn.com", "tbcdn.cn"))):
                raise ValueError("Only marketplace HTTPS image URLs are accepted.")
        return values

    def to_record_item(self) -> dict:
        return {
            "商品ID": self.id, "商品标题": self.title, "商品描述": self.description,
            "当前售价": self.price, "商品原价": self.original_price,
            "发货地区": self.area, "卖家昵称": self.seller_nickname,
            "商品链接": "https://www.goofish.com/item?id=" + self.id,
            "商品图片列表": self.images, "商品主图链接": self.images[0] if self.images else "",
            "商品标签": self.tags, "发布时间": self.publish_time,
            "“想要”人数": self.wants, "浏览量": self.views,
        }


class BrowserReport(BaseModel):
    client_id: str = Field(min_length=8, max_length=100)
    run_id: str = Field(min_length=1, max_length=100)
    command_id: str = Field(min_length=1, max_length=100)
    outcome: Literal["ok", "attention", "error"] = "ok"
    message: str = Field(default="", max_length=2000)
    items: list[BrowserItem] = Field(default_factory=list, max_length=200)
    item: BrowserItem | None = None
    seller: dict = Field(default_factory=dict)
    has_more: bool = False
    region_applied: bool = False
    personal_applied: bool = False


class BrowserResume(BaseModel):
    client_id: str = Field(min_length=8, max_length=100)
    run_id: str = Field(min_length=1, max_length=100)
    command_id: str | None = Field(default=None, max_length=100)


class BrowserSetup(BaseModel):
    launch: bool = False
    profile_directory: str | None = Field(default=None, max_length=100)
