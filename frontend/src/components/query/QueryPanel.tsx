import { useCallback, useMemo } from "react";
import type { DragItem, ModelSpec } from "../../types";
import { useQueryDraft } from "../../state/query";
import FieldPalette from "./FieldPalette";
import DropZone from "./DropZone";
import FilterZone from "./FilterZone";
import ResultTable from "./ResultTable";
import SqlPreview from "./SqlPreview";

interface Props {
  model: ModelSpec;
  modelVersion: number;
}

export default function QueryPanel({ model, modelVersion }: Props) {
  const q = useQueryDraft(model, modelVersion);

  const handleDrop = useCallback(
    (zone: "rows" | "columns" | "measures", item: DragItem) => {
      q.addItem(zone, item);
    },
    [q]
  );

  const handleFilterDrop = useCallback(
    (item: DragItem) => {
      if (item.kind === "measure") {
        const exists = q.draft.filters.some(
          (f) => f.kind === "measure" && f.target === item.id
        );
        if (!exists)
          q.upsertFilter({ kind: "measure", target: item.id, op: "gt", value: 0 });
        return;
      }
      // 维度或计算字段
      const isCalc = model.calculated_fields.some((c) => c.id === item.id);
      const target = item.id;
      if (isCalc) {
        const exists = q.draft.filters.some(
          (f) => f.kind === "calculated" && f.target === target
        );
        if (!exists)
          q.upsertFilter({
            kind: "calculated",
            target,
            op: "gt",
            value: 0,
          });
        return;
      }
      const exists = q.draft.filters.some(
        (f) => f.kind === "dimension" && f.target === target
      );
      if (!exists)
        q.upsertFilter({ kind: "dimension", target, op: "eq", value: null });
    },
    [model, q]
  );

  const activeFilters = useMemo(
    () =>
      q.draft.filters.filter((f) => {
        if (f.op === "is_null" || f.op === "is_not_null") return true;
        if (Array.isArray(f.value)) return f.value.length > 0;
        return f.value !== null && f.value !== "" && f.value !== undefined;
      }),
    [q.draft.filters]
  );

  return (
    <div className="query-layout">
      <FieldPalette model={model} />
      <section className="builder">
        <div className="zones">
          <div className="zone-row">
            <DropZone
              zone="rows"
              title="行"
              subtitle="分组维度，按顺序钻取"
              ids={q.draft.rows}
              model={model}
              onDrop={(item) => handleDrop("rows", item)}
              onRemove={(id) => q.removeItem("rows", id)}
            />
            <DropZone
              zone="columns"
              title="列"
              subtitle="额外的分组维度"
              ids={q.draft.columns}
              model={model}
              onDrop={(item) => handleDrop("columns", item)}
              onRemove={(id) => q.removeItem("columns", id)}
            />
          </div>
          <div className="zone-row">
            <DropZone
              zone="measures"
              title="数值"
              subtitle="求和 / 计数 / 平均 / 去重 …"
              ids={q.draft.measures}
              model={model}
              onDrop={(item) => handleDrop("measures", item)}
              onRemove={(id) => q.removeItem("measures", id)}
            />
            <FilterZone
              model={model}
              filters={activeFilters}
              onUpsert={q.upsertFilter}
              onRemove={q.removeFilter}
              onDropItem={handleFilterDrop}
            />
          </div>
        </div>

        <div className="toolbar">
          <button className="btn" onClick={q.clearAll}>
            清空
          </button>
          <span className="toolbar-hint">
            调整拖拽后自动查询；点击维度值可沿层级下钻
          </span>
          {q.loading && <span className="spinner">查询中…</span>}
        </div>

        {q.error && <div className="banner error">{q.error}</div>}

        {q.result && (
          <>
            <ResultTable
              model={model}
              result={q.result}
              onDrill={q.drillInto}
            />
            <SqlPreview result={q.result} />
          </>
        )}
        {!q.hasContent && !q.loading && (
          <div className="empty-state">
            从左侧把维度拖到「行 / 列」，把度量拖到「数值」，结果会实时出现在这里。
          </div>
        )}
      </section>
    </div>
  );
}
