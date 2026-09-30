import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type {
  DragItem,
  FilterSpec,
  ModelSpec,
  QueryResponse,
  QuerySpec,
} from "../types";

export interface QueryDraft {
  rows: string[];
  columns: string[];
  measures: string[];
  filters: FilterSpec[];
  limit: number;
}

export const emptyDraft: QueryDraft = {
  rows: [],
  columns: [],
  measures: [],
  filters: [],
  limit: 200,
};

function toSpec(d: QueryDraft): QuerySpec {
  return {
    rows: d.rows.map((dimension) => ({ dimension })),
    columns: d.columns.map((dimension) => ({ dimension })),
    measures: d.measures,
    filters: d.filters,
    limit: d.limit,
  };
}

/** 在模型上找某维度层级中的下一级（更细）维度 id。 */
export function nextLevelId(model: ModelSpec, dimId: string): string | null {
  const dim = model.dimensions.find((x) => x.id === dimId);
  if (!dim || dim.hierarchy_id == null || dim.level_index == null)
    return null;
  const h = model.hierarchies.find((x) => x.id === dim.hierarchy_id);
  if (!h) return null;
  return h.dimension_ids[dim.level_index + 1] ?? null;
}

export function useQueryDraft(model: ModelSpec | null, modelVersion: number) {
  const [draft, setDraft] = useState<QueryDraft>(emptyDraft);
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);

  const update = useCallback((fn: (d: QueryDraft) => QueryDraft) => {
    setDraft((d) => fn(structuredClone(d)));
  }, []);

  const addItem = useCallback(
    (zone: "rows" | "columns" | "measures", item: DragItem) => {
      update((d) => {
        if (item.kind === "measure") {
          if (zone !== "measures") return d;
          return d.measures.includes(item.id)
            ? d
            : { ...d, measures: [...d.measures, item.id] };
        }
        if (zone === "measures") return d;
        if (d[zone].includes(item.id)) return d;
        const other = zone === "rows" ? "columns" : "rows";
        return {
          ...d,
          [zone]: [...d[zone], item.id],
          [other]: d[other].filter((x) => x !== item.id),
        };
      });
    },
    [update]
  );

  const removeItem = useCallback(
    (zone: "rows" | "columns" | "measures", id: string) => {
      update((d) => ({ ...d, [zone]: d[zone].filter((x) => x !== id) }));
    },
    [update]
  );

  const upsertFilter = useCallback((f: FilterSpec) => {
    setDraft((d) => {
      const rest = d.filters.filter(
        (x) => !(x.kind === f.kind && x.target === f.target)
      );
      return { ...d, filters: [...rest, f] };
    });
  }, []);

  const removeFilter = useCallback(
    (kind: FilterSpec["kind"], target: string) => {
      update((d) => ({
        ...d,
        filters: d.filters.filter(
          (x) => !(x.kind === kind && x.target === target)
        ),
      }));
    },
    [update]
  );

  const clearAll = useCallback(() => setDraft(emptyDraft), []);

  /** 点击层级维度的聚合值：追加更细一级 + 父级取值过滤。 */
  const drillInto = useCallback(
    (dimensionId: string, value: unknown) => {
      if (!model) return;
      const child = nextLevelId(model, dimensionId);
      setDraft((d) => {
        let zone: "rows" | "columns" | null = d.rows.includes(dimensionId)
          ? "rows"
          : d.columns.includes(dimensionId)
          ? "columns"
          : null;
        if (!zone) return d;
        const next = { ...d };
        if (child && !d[zone].includes(child)) {
          const idx = d[zone].indexOf(dimensionId);
          const list = [...d[zone]];
          list.splice(idx + 1, 0, child);
          next[zone] = list;
        }
        next.filters = [
          ...d.filters.filter(
            (f) =>
              !(f.kind === "dimension" && f.target === dimensionId)
          ),
          { kind: "dimension" as const, target: dimensionId, op: "eq" as const, value },
        ];
        return next;
      });
    },
    [model]
  );

  /** 上卷：移除某层级维度及其父级取值过滤的更细层级。 */
  const rollUp = useCallback((dimensionId: string) => {
    setDraft((d) => {
      const inRows = d.rows.includes(dimensionId);
      const inCols = d.columns.includes(dimensionId);
      if (!inRows && !inCols) return d;
      const zone = inRows ? "rows" : "columns";
      return {
        ...d,
        [zone]: d[zone].filter((x) => x !== dimensionId),
        filters: d.filters.filter(
          (f) => !(f.kind === "dimension" && f.target === dimensionId)
        ),
      };
    });
  }, []);

  const spec: QuerySpec = useMemo(() => toSpec(draft), [draft]);

  const hasContent =
    draft.rows.length + draft.columns.length + draft.measures.length > 0;

  useEffect(() => {
    if (!hasContent) {
      setResult(null);
      setError(null);
      return;
    }
    const my = ++seq.current;
    setLoading(true);
    setError(null);
    const timer = setTimeout(async () => {
      try {
        const resp = await api.runQuery(spec);
        if (my !== seq.current) return;
        setResult(resp);
      } catch (e) {
        if (my !== seq.current) return;
        setError((e as Error).message);
      } finally {
        if (my === seq.current) setLoading(false);
      }
    }, 250);
    return () => clearTimeout(timer);
  }, [spec, hasContent, modelVersion]);

  return {
    draft,
    setDraft,
    update,
    addItem,
    removeItem,
    upsertFilter,
    removeFilter,
    clearAll,
    drillInto,
    rollUp,
    spec,
    result,
    loading,
    error,
    hasContent,
  };
}
