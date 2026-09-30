// 与后端 Pydantic 模型对应的类型定义

export type DataType = "integer" | "number" | "text" | "date" | "boolean";
export type Cardinality = "1:1" | "1:N" | "N:1" | "N:N";
export type AggKind =
  | "sum" | "count" | "count_distinct" | "avg" | "min" | "max";

export interface ColumnSpec {
  name: string;
  data_type: DataType;
  label?: string | null;
}

export interface TableSpec {
  name: string;
  label: string;
  schema_name: string;
  primary_key: string;
  columns: ColumnSpec[];
}

export interface RelationSpec {
  id: string;
  label: string;
  left_table: string;
  left_column: string;
  right_table: string;
  right_column: string;
  cardinality: Cardinality;
}

export interface DimensionSpec {
  id: string;
  name: string;
  table: string;
  column?: string | null;
  expression?: string | null;
  data_type: DataType;
  hierarchy_id?: string | null;
  level_index?: number | null;
}

export interface HierarchySpec {
  id: string;
  name: string;
  dimension_ids: string[];
}

export interface MeasureSpec {
  id: string;
  name: string;
  table: string;
  agg: AggKind;
  column?: string | null;
  expression?: string | null;
  filter?: string | null;
}

export interface CalculatedFieldSpec {
  id: string;
  name: string;
  table: string;
  expression: string;
  data_type: DataType;
  join_tables?: string[];
}

export interface ModelSpec {
  tables: TableSpec[];
  relations: RelationSpec[];
  dimensions: DimensionSpec[];
  hierarchies: HierarchySpec[];
  measures: MeasureSpec[];
  calculated_fields: CalculatedFieldSpec[];
}

// ---- 查询意图 ----
export type FilterOp =
  | "eq" | "ne" | "in" | "not_in" | "between"
  | "gt" | "gte" | "lt" | "lte" | "contains"
  | "is_null" | "is_not_null";

export type FilterKind = "dimension" | "measure" | "calculated";

export interface GroupEntry { dimension: string }
export interface FilterSpec {
  kind: FilterKind;
  target: string;
  op: FilterOp;
  value?: unknown;
}

export interface QuerySpec {
  rows: GroupEntry[];
  columns: GroupEntry[];
  measures: string[];
  filters: FilterSpec[];
  limit: number;
  drill?: string | null;
  execute?: boolean;
}

export interface ResultColumn {
  key: string;
  label: string;
  data_type: string;
}

export interface QueryResponse {
  sql: string;
  params: unknown[];
  joined_tables: string[];
  fanout_tables: string[];
  columns: ResultColumn[];
  rows?: unknown[][];
  row_count?: number;
  truncated?: boolean;
  spec: QuerySpec;
}

export interface ExpressionValidation {
  valid: boolean;
  referenced?: string[];
  tables?: string[];
  error?: { message: string; line: number; col: number; end_col?: number | null };
}

// 拖拽载体
export type DragItem =
  | { kind: "dimension"; id: string }
  | { kind: "measure"; id: string }
  | { kind: "calculated"; id: string };
