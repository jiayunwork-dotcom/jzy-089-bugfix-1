"""语义建模层的数据结构。

模型描述（ModelSpec）：
- tables:    物理表（含 schema、主键、列），可来自数据库自省
- relations: 两张表之间的关联（字段对 + 1:1 / 1:N / N:1 / N:N）
- dimensions: 维度，物理列或沙箱表达式，可隶属某条层级
- hierarchies: 自上而下的钻取路径（年→季→月→日、大区→省→市）
- measures: 度量（聚合 + 数值字段/表达式 + 可选过滤）
- calculated_fields: 计算字段（沙箱表达式，保存即校验）
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

Cardinality = Literal["1:1", "1:N", "N:1", "N:N"]
DataType = Literal["integer", "number", "text", "date", "boolean"]
AggKind = Literal["sum", "count", "count_distinct", "avg", "min", "max"]


class ColumnSpec(BaseModel):
    name: str
    data_type: DataType
    label: Optional[str] = None


class TableSpec(BaseModel):
    name: str
    label: str
    schema_name: str = "biz"
    primary_key: str = "id"
    columns: List[ColumnSpec] = Field(default_factory=list)

    def qname(self) -> str:
        return f'"{self.schema_name}"."{self.name}"'


class RelationSpec(BaseModel):
    id: str
    label: str
    left_table: str
    left_column: str
    right_table: str
    right_column: str
    # 从 left 看向 right 的基数，例如 orders -> order_items 为 1:N
    cardinality: Cardinality


class DimensionSpec(BaseModel):
    id: str
    name: str
    table: str
    column: Optional[str] = None          # 与 expression 二选一
    expression: Optional[str] = None      # 走沙箱编译
    data_type: DataType
    hierarchy_id: Optional[str] = None
    level_index: Optional[int] = None     # 在层级中的序号（从 0 开始）


class HierarchySpec(BaseModel):
    id: str
    name: str
    dimension_ids: List[str]              # 自上而下：粗 -> 细


class MeasureSpec(BaseModel):
    id: str
    name: str
    table: str
    agg: AggKind
    column: Optional[str] = None          # 聚合基字段（count 可空=count 行）
    expression: Optional[str] = None      # 或聚合基表达式（走沙箱）
    filter: Optional[str] = None          # 度量级过滤（沙箱布尔表达式）


class CalculatedFieldSpec(BaseModel):
    id: str
    name: str
    table: str
    expression: str                       # 必须通过沙箱校验
    data_type: DataType
    # 表达式里允许引用的额外表（必须是沿多对一关联可达的表，不会引入放大）
    join_tables: List[str] = Field(default_factory=list)


class ModelSpec(BaseModel):
    tables: List[TableSpec] = Field(default_factory=list)
    relations: List[RelationSpec] = Field(default_factory=list)
    dimensions: List[DimensionSpec] = Field(default_factory=list)
    hierarchies: List[HierarchySpec] = Field(default_factory=list)
    measures: List[MeasureSpec] = Field(default_factory=list)
    calculated_fields: List[CalculatedFieldSpec] = Field(default_factory=list)

    # ---- 便捷索引 ----
    def table(self, name: str) -> TableSpec:
        for t in self.tables:
            if t.name == name:
                return t
        raise KeyError(f"模型中不存在表：{name}")

    def has_table(self, name: str) -> bool:
        return any(t.name == name for t in self.tables)

    def dimension(self, dim_id: str) -> DimensionSpec:
        for d in self.dimensions:
            if d.id == dim_id:
                return d
        raise KeyError(f"未知维度：{dim_id}")

    def measure(self, measure_id: str) -> MeasureSpec:
        for m in self.measures:
            if m.id == measure_id:
                return m
        raise KeyError(f"未知度量：{measure_id}")

    def calculated(self, calc_id: str) -> CalculatedFieldSpec:
        for c in self.calculated_fields:
            if c.id == calc_id:
                return c
        raise KeyError(f"未知计算字段：{calc_id}")

    def hierarchy_of(self, dim_id: str) -> Optional[HierarchySpec]:
        d = self.dimension(dim_id)
        if not d.hierarchy_id:
            return None
        for h in self.hierarchies:
            if h.id == d.hierarchy_id:
                return h
        return None

    def next_level(self, dim_id: str) -> Optional[DimensionSpec]:
        """钻取：返回该维度在层级中的下一级（更细）维度。"""
        h = self.hierarchy_of(dim_id)
        if h is None:
            return None
        cur = self.dimension(dim_id)
        if cur.level_index is None or cur.level_index + 1 >= len(h.dimension_ids):
            return None
        return self.dimension(h.dimension_ids[cur.level_index + 1])
