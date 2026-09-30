import { useMemo, useState } from "react";
import type { CalculatedFieldSpec, DimensionSpec, ModelSpec } from "../../types";
import { startDrag } from "../dnd";

interface Props {
  model: ModelSpec;
}

type Bin = "dimension" | "measure" | "calculated";

export default function FieldPalette({ model }: Props) {
  const [q, setQ] = useState("");
  const dimById = useMemo(
    () => new Map(model.dimensions.map((d) => [d.id, d])),
    [model]
  );

  const groups = useMemo(() => {
    const keyword = q.trim().toLowerCase();
    const hit = (name: string, table?: string) =>
      !keyword ||
      name.toLowerCase().includes(keyword) ||
      (table ?? "").toLowerCase().includes(keyword);

    const dims = model.dimensions.filter((d) => hit(d.name, d.table));
    const meas = model.measures.filter((m) => hit(m.name, m.table));
    const calcs = model.calculated_fields.filter((c) => hit(c.name, c.table));
    return { dims, meas, calcs };
  }, [model, q]);

  return (
    <aside className="palette">
      <input
        className="search"
        placeholder="搜索维度 / 度量…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <FieldGroup
        title="维度"
        hint="拖入 行 / 列 / 筛选"
      >
        {groups.dims.map((d) => (
          <DimensionChip key={d.id} dim={d} model={model} />
        ))}
      </FieldGroup>

      <FieldGroup title="度量" hint="拖入 数值 / 筛选">
        {groups.meas.map((m) => (
          <div
            key={m.id}
            className="chip measure"
            draggable
            onDragStart={(e) =>
              startDrag(e, { kind: "measure", id: m.id })
            }
            title={`${m.agg.toUpperCase()}(${m.column ?? m.expression ?? "*"}) · ${m.table}`}
          >
            <span className="chip-name">{m.name}</span>
            <span className="chip-meta">{m.agg}</span>
          </div>
        ))}
      </FieldGroup>

      <FieldGroup title="计算字段" hint="行/列分组，或作为筛选">
        {groups.calcs.map((c: CalculatedFieldSpec) => (
          <div
            key={c.id}
            className="chip calc"
            draggable
            onDragStart={(e) =>
              startDrag(e, { kind: "dimension", id: c.id })
            }
            title={c.expression}
          >
            <span className="chip-name">{c.name}</span>
            <span className="chip-meta">ƒ</span>
          </div>
        ))}
      </FieldGroup>

      <div className="palette-foot">
        共 {model.dimensions.length} 个维度 · {model.measures.length} 个度量 ·{" "}
        {model.calculated_fields.length} 个计算字段
      </div>
    </aside>
  );
}

function FieldGroup({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="field-group">
      <header>
        <h3>{title}</h3>
        {hint && <span className="group-hint">{hint}</span>}
      </header>
      <div className="chips">{children}</div>
    </section>
  );
}

function DimensionChip({
  dim,
  model,
}: {
  dim: DimensionSpec;
  model: ModelSpec;
}) {
  const hierarchy =
    dim.hierarchy_id != null
      ? model.hierarchies.find((h) => h.id === dim.hierarchy_id)
      : null;
  const canDrill =
    hierarchy != null &&
    dim.level_index != null &&
    dim.level_index < hierarchy.dimension_ids.length - 1;
  return (
    <div
      className="chip dimension"
      draggable
      onDragStart={(e) => startDrag(e, { kind: "dimension", id: dim.id })}
      title={
        hierarchy
          ? `${hierarchy.name} 第 ${(dim.level_index ?? 0) + 1} 级${
              canDrill ? "（可下钻）" : "（最细级）"
            }`
          : dim.column ?? dim.expression ?? ""
      }
    >
      <span className="chip-name">{dim.name}</span>
      {hierarchy && (
        <span className="chip-meta" title={hierarchy.name}>
          ▸{canDrill ? "▸" : ""}
        </span>
      )}
    </div>
  );
}
