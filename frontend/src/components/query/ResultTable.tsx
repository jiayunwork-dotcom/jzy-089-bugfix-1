import { useMemo, useState } from "react";
import type { ModelSpec, QueryResponse } from "../../types";
import { nextLevelId } from "../../state/query";

interface Props {
  model: ModelSpec;
  result: QueryResponse;
  onDrill: (dimensionId: string, value: unknown) => void;
}

type SortDir = "asc" | "desc";

export default function ResultTable({ model, result, onDrill }: Props) {
  const [sortCol, setSortCol] = useState<number | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const dimIds = useMemo(() => {
    const ids = [...result.spec.rows.map((r) => r.dimension),
                 ...result.spec.columns.map((c) => c.dimension)];
    return ids;
  }, [result.spec]);

  const sorted = useMemo(() => {
    const rows = [...(result.rows ?? [])];
    if (sortCol == null) return rows;
    const dir = sortDir === "asc" ? 1 : -1;
    rows.sort((a, b) => {
      const x = a[sortCol];
      const y = b[sortCol];
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      if (typeof x === "number" && typeof y === "number")
        return (x - y) * dir;
      return String(x).localeCompare(String(y), "zh") * dir;
    });
    return rows;
  }, [result.rows, sortCol, sortDir]);

  const toggleSort = (i: number) => {
    if (sortCol === i) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortCol(i);
      setSortDir("asc");
    }
  };

  const canDrill = (colKey: string) =>
    dimIds.includes(colKey) && nextLevelId(model, colKey) != null;

  return (
    <div className="table-wrap">
      <table className="result-table">
        <thead>
          <tr>
            {result.columns.map((c, i) => (
              <th
                key={c.key}
                className={c.data_type === "number" ? "num" : ""}
                onClick={() => toggleSort(i)}
              >
                <span>{c.label}</span>
                {canDrill(c.key) && <span className="drill-glyph" title="点击聚合值可下钻">▸</span>}
                <span className="sort-glyph">
                  {sortCol === i ? (sortDir === "asc" ? "▲" : "▼") : ""}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 && (
            <tr>
              <td colSpan={result.columns.length} className="empty">
                查询无结果
              </td>
            </tr>
          )}
          {sorted.map((row, ri) => (
            <tr key={ri}>
              {result.columns.map((c, ci) => {
                const drillable =
                  dimIds.includes(c.key) && nextLevelId(model, c.key) != null;
                return (
                  <td
                    key={c.key}
                    className={
                      (c.data_type === "number" ? "num " : "") +
                      (drillable ? "drillable" : "")
                    }
                    onClick={
                      drillable
                        ? () => onDrill(c.key, row[ci])
                        : undefined
                    }
                    title={drillable ? "点击下钻到更细层级" : undefined}
                  >
                    {formatCell(row[ci], c.data_type)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="table-foot">
        {result.row_count} 行
        {result.truncated && <span className="warn">（已达行数上限）</span>}
      </div>
    </div>
  );
}

function formatCell(v: unknown, dataType: string): string {
  if (v == null) return "∅";
  if (typeof v === "number") {
    if (Number.isInteger(v)) return v.toLocaleString("zh-CN");
    return v.toLocaleString("zh-CN", { maximumFractionDigits: 4 });
  }
  if (dataType === "date" && typeof v === "string") {
    // DATE_TRUNC 产出 ISO 时间戳，只展示日期部分
    return v.length > 10 ? v.slice(0, 10) : v;
  }
  if (typeof v === "boolean") return v ? "是" : "否";
  return String(v);
}
