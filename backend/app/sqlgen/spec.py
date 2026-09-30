"""拖拽意图（查询意图）的结构化描述 —— SQL 生成器唯一接受的输入。

前端四个投放区映射为：
- rows:     行维度（进 GROUP BY，结果表里的左侧分组列）
- columns:  列维度（同样进 GROUP BY，前端做透视展开）
- measures: 数值区的度量 / 可聚合计算字段（进聚合表达式）
- filters:  筛选区条件（维度 -> WHERE；度量 -> HAVING）
"""
from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field

FilterOp = Literal[
    "eq", "ne", "in", "not_in", "between",
    "gt", "gte", "lt", "lte", "contains",
    "is_null", "is_not_null",
]
FilterKind = Literal["dimension", "measure", "calculated"]


class GroupEntry(BaseModel):
    dimension: str


class FilterSpec(BaseModel):
    kind: FilterKind
    target: str
    op: FilterOp
    value: Optional[Any] = None  # eq/... 单值；in 列表；between [lo, hi]


class QuerySpec(BaseModel):
    rows: List[GroupEntry] = Field(default_factory=list)
    columns: List[GroupEntry] = Field(default_factory=list)
    measures: List[str] = Field(default_factory=list)
    filters: List[FilterSpec] = Field(default_factory=list)
    limit: int = 200

    def group_dimensions(self) -> List[str]:
        """行、列维度按投放顺序进入 GROUP BY（稳定序）。"""
        return [e.dimension for e in self.rows] + [e.dimension for e in self.columns]

    def dimension_filters(self) -> List[FilterSpec]:
        return [f for f in self.filters if f.kind != "measure"]

    def measure_filters(self) -> List[FilterSpec]:
        return [f for f in self.filters if f.kind == "measure"]


def apply_drill(model, spec: QuerySpec, dimension_id: str) -> QuerySpec:
    """下钻：在该维度所在位置后面"追加"层级中的更细一级。

    内核规则（有自动化测试守住）：
    - 只改变分组粒度：追加更细维度，度量与所有过滤原样保留；
    - 已在最细一级或该维度没有层级时，原样返回（幂等）；
    - 点击值产生的父级过滤由前端作为普通 filter 加入，不属于本函数职责。
    """
    nxt = model.next_level(dimension_id)
    if nxt is None:
        return spec.model_copy(deep=True)

    def inject(entries: List[GroupEntry]) -> List[GroupEntry]:
        out: List[GroupEntry] = []
        for e in entries:
            out.append(e)
            if (e.dimension == dimension_id
                    and not any(x.dimension == nxt.id
                                for x in spec.rows + spec.columns)):
                out.append(GroupEntry(dimension=nxt.id))
        return out

    new = spec.model_copy(deep=True)
    new.rows = inject(new.rows)
    new.columns = inject(new.columns)
    return new
