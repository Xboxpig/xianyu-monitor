"""AI reasoning effort 配置。"""

from typing import Literal, TypeAlias


ReasoningEffort: TypeAlias = Literal["low", "medium", "high", "xhigh", "max"]
REASONING_EFFORT_VALUES: tuple[ReasoningEffort, ...] = (
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)
DEFAULT_REASONING_EFFORT: ReasoningEffort = "medium"


def normalize_reasoning_effort(value: object) -> ReasoningEffort:
    """将环境变量等外部输入规范化为受支持的 reasoning effort。"""
    normalized = str(value or "").strip().lower()
    if normalized in REASONING_EFFORT_VALUES:
        return normalized  # type: ignore[return-value]
    return DEFAULT_REASONING_EFFORT
