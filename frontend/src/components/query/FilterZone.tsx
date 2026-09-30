import { useEffect, useState } from "react";
import { api } from "../../api";
import type {
  DragItem,
  FilterOp,
  FilterSpec,
  ModelSpec,
} from "../../types";
import { readDrag } from "../dnd";

interface Props {
  model: ModelSpec;
  filters: FilterSpec[];
  onUpsert: (f: FilterSpec) => void;
  onRemove: (kind: FilterSpec["kind"], target: string) => void;
  onDropItem: (item: DragItem) => void;
}

const OPS: { value: FilterOp; label: string; types: string[] }[] = [
  { value: "eq", label: "等于", types: ["text", "number", "date", "boolean", "integer"] },
  { value: "ne", label: "不等于", types: ["text", "number", "date", "integer"] },
  { value: "in", label: "属于", types: ["text", "number", "integer"] },
  { value: "not_in", label: "不属于", types: ["text", "number", "integer"] },
  { value: "between", label: "区间", types: ["number", "date", "integer"] },
  { value: "gt", label: ">", types: ["number", "date", "integer"] },
  { value: "gte", label: "≥", types: ["number", "date", "integer"] },
  { value: "lt", label: "<", types: ["number", "date", "integer"] },
  { value: "lte", label: "≤", types: ["number", "date", "integer"] },
  { value: "contains", label: "包含", types: ["text"] },
  { value: "is_null", label: "为空", types: ["text", "number", "date", "integer", "boolean"] },
  { value: "is_not_null", label: "非空", types: ["text", "number", "date", "integer", "boolean"] },
];

export default function FilterZone({
  model,
  filters,
  onUpsert,
  onRemove,
  onDropItem,
}: Props) {
  const [over, setOver] = useState(false);

  return (
    <div
      className={`drop-zone filter-zone ${over ? "over" : ""} ${
        filters.length ? "filled" : ""
      }`}
      onDragOver={(e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const item = readDrag(e);
        if (item) onDropItem(item);
      }}
    >
      <div className="drop-head">
        <strong>筛选</strong>
        <span className="drop-sub">维度/度量拖入后选择条件，全部参数化下发</span>
      </div>
      <div className="drop-body filters">
        {filters.length === 0 && (
          <span className="drop-placeholder">把字段拖到这里添加筛选</span>
        )}
        {filters.map((f) => (
          <FilterRow
            key={`${f.kind}:${f.target}`}
            model={model}
            filter={f}
            onUpsert={onUpsert}
            onRemove={onRemove}
          />
        ))}
      </div>
    </div>
  );
}

function targetInfo(model: ModelSpec, f: FilterSpec) {
  if (f.kind === "measure") {
    const m = model.measures.find((x) => x.id === f.target);
    return m
      ? { name: m.name, dataType: "number" as const }
      : { name: f.target, dataType: "number" as const };
  }
  if (f.kind === "calculated") {
    const c = model.calculated_fields.find((x) => x.id === f.target);
    return c
      ? { name: c.name, dataType: c.data_type }
      : { name: f.target, dataType: "text" as const };
  }
  const d = model.dimensions.find((x) => x.id === f.target);
  return d
    ? { name: d.name, dataType: d.data_type }
    : { name: f.target, dataType: "text" as const };
}

function FilterRow({
  model,
  filter,
  onUpsert,
  onRemove,
}: {
  model: ModelSpec;
  filter: FilterSpec;
  onUpsert: (f: FilterSpec) => void;
  onRemove: (kind: FilterSpec["kind"], target: string) => void;
}) {
  const info = targetInfo(model, filter);
  const usableOps = OPS.filter((o) => o.types.includes(info.dataType));
  const hasValue =
    filter.op !== "is_null" && filter.op !== "is_not_null";
  const isMulti = filter.op === "in" || filter.op === "not_in";
  const isRange = filter.op === "between";

  const patch = (op: FilterOp, value?: unknown) =>
    onUpsert({ ...filter, op, value });

  return (
    <div className="filter-row">
      <span className="filter-target" title={`${filter.kind} · ${info.dataType}`}>
        {info.name}
      </span>
      <select
        value={filter.op}
        onChange={(e) => {
          const op = e.target.value as FilterOp;
          if (op === "is_null" || op === "is_not_null") {
            onUpsert({ ...filter, op, value: null });
          } else {
            patch(op, filter.value);
          }
        }}
      >
        {usableOps.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      {hasValue && filter.kind === "dimension" && info.dataType === "text" && (
        <EnumValueEditor filter={filter} onValue={(v) => patch(filter.op, v)} />
      )}
      {hasValue && filter.kind === "dimension" && info.dataType === "date" && (
        <DateValueEditor
          op={filter.op}
          value={filter.value}
          onValue={(v) => patch(filter.op, v)}
        />
      )}
      {hasValue &&
        !(filter.kind === "dimension" &&
          (info.dataType === "text" || info.dataType === "date")) && (
          <GenericValueEditor
            isMulti={isMulti}
            isRange={isRange}
            dataType={info.dataType}
            value={filter.value}
            onValue={(v) => patch(filter.op, v)}
          />
        )}
      <button
        className="pill-x"
        onClick={() => onRemove(filter.kind, filter.target)}
        title="删除筛选"
      >
        ×
      </button>
    </div>
  );
}

function EnumValueEditor({
  filter,
  onValue,
}: {
  filter: FilterSpec;
  onValue: (v: unknown) => void;
}) {
  const [options, setOptions] = useState<(string | number)[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    void api
      .dimensionValues(filter.target)
      .then((r) => alive && setOptions(r.values))
      .catch(() => undefined)
      .finally(() => alive && setLoaded(true));
    return () => {
      alive = false;
    };
  }, [filter.target]);

  if (filter.op === "in" || filter.op === "not_in") {
    const selected = Array.isArray(filter.value)
      ? (filter.value as (string | number)[])
      : [];
    return (
      <select
        multiple
        className="multi-select"
        value={selected.map(String)}
        onChange={(e) => {
          const picked = Array.from(e.target.selectedOptions).map((o) => o.value);
          onValue(picked);
        }}
      >
        {options.map((v) => (
          <option key={String(v)} value={String(v)}>
            {String(v)}
          </option>
        ))}
      </select>
    );
  }
  if (filter.op === "contains") {
    return (
      <input
        value={(filter.value as string) ?? ""}
        onChange={(e) => onValue(e.target.value)}
        placeholder="包含文本"
      />
    );
  }
  return (
    <select
      value={filter.value == null ? "" : String(filter.value)}
      onChange={(e) => onValue(e.target.value)}
    >
      <option value="" disabled>
        {loaded ? "请选择" : "加载中…"}
      </option>
      {options.map((v) => (
        <option key={String(v)} value={String(v)}>
          {String(v)}
        </option>
      ))}
    </select>
  );
}

function DateValueEditor({
  op,
  value,
  onValue,
}: {
  op: FilterOp;
  value: unknown;
  onValue: (v: unknown) => void;
}) {
  if (op === "between") {
    const range = Array.isArray(value) ? (value as string[]) : ["", ""];
    return (
      <span className="range">
        <input
          type="date"
          value={range[0] ?? ""}
          onChange={(e) => onValue([e.target.value, range[1] ?? ""])}
        />
        <em>至</em>
        <input
          type="date"
          value={range[1] ?? ""}
          onChange={(e) => onValue([range[0] ?? "", e.target.value])}
        />
      </span>
    );
  }
  return (
    <input
      type="date"
      value={(value as string) ?? ""}
      onChange={(e) => onValue(e.target.value)}
    />
  );
}

function GenericValueEditor({
  isMulti,
  isRange,
  dataType,
  value,
  onValue,
}: {
  isMulti: boolean;
  isRange: boolean;
  dataType: string;
  value: unknown;
  onValue: (v: unknown) => void;
}) {
  const numeric = dataType === "number" || dataType === "integer";
  if (isRange) {
    const range = Array.isArray(value) ? (value as string[]) : ["", ""];
    const num = numeric;
    return (
      <span className="range">
        <input
          type={num ? "number" : "date"}
          value={range[0] ?? ""}
          onChange={(e) =>
            onValue([
              num ? NumberOrNull(e.target.value) : e.target.value,
              range[1] ?? "",
            ])
          }
        />
        <em>至</em>
        <input
          type={num ? "number" : "date"}
          value={range[1] ?? ""}
          onChange={(e) =>
            onValue([
              range[0] ?? "",
              num ? NumberOrNull(e.target.value) : e.target.value,
            ])
          }
        />
      </span>
    );
  }
  if (isMulti) {
    const text = Array.isArray(value) ? (value as unknown[]).join(", ") : "";
    return (
      <input
        placeholder="多个值用逗号分隔"
        value={text}
        onChange={(e) =>
          onValue(
            e.target.value
              .split(",")
              .map((s) => s.trim())
              .filter(Boolean)
              .map((s) => (numeric ? NumberOrNull(s) : s))
          )
        }
      />
    );
  }
  return (
    <input
      type={numeric ? "number" : "text"}
      value={value == null ? "" : String(value)}
      onChange={(e) =>
        onValue(numeric ? NumberOrNull(e.target.value) : e.target.value)
      }
      placeholder="值"
    />
  );
}

function NumberOrNull(s: string): number | string {
  if (s.trim() === "") return "";
  const n = Number(s);
  return Number.isNaN(n) ? s : n;
}
