import { useState } from "react";
import type {
  AggKind,
  CalculatedFieldSpec,
  Cardinality,
  DataType,
  DimensionSpec,
  HierarchySpec,
  MeasureSpec,
  ModelSpec,
  RelationSpec,
} from "../../types";
import { api } from "../../api";

interface Props {
  model: ModelSpec;
  onSaved: () => void;
}

type Section = "tables" | "relations" | "dimensions" | "measures" | "calc";

export default function ModelerPanel({ model, onSaved }: Props) {
  const [draft, setDraft] = useState<ModelSpec>(() =>
    JSON.parse(JSON.stringify(model))
  );
  const [section, setSection] = useState<Section>("relations");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const dirty = JSON.stringify(draft) !== JSON.stringify(model);

  const patch = (fn: (m: ModelSpec) => ModelSpec) =>
    setDraft((m) => fn(JSON.parse(JSON.stringify(m))));

  const save = async () => {
    setSaveError(null);
    try {
      await api.saveModel(draft);
      setSavedAt(new Date().toLocaleTimeString("zh-CN"));
      onSaved();
    } catch (e) {
      setSaveError((e as Error).message);
    }
  };

  const tabs: { key: Section; label: string }[] = [
    { key: "tables", label: `表（${draft.tables.length}）` },
    { key: "relations", label: `关联（${draft.relations.length}）` },
    { key: "dimensions", label: `维度 / 层级（${draft.dimensions.length}）` },
    { key: "measures", label: `度量（${draft.measures.length}）` },
    { key: "calc", label: `计算字段（${draft.calculated_fields.length}）` },
  ];

  return (
    <div className="modeler">
      <div className="modeler-bar">
        <div className="subtabs">
          {tabs.map((t) => (
            <button
              key={t.key}
              className={section === t.key ? "subtab active" : "subtab"}
              onClick={() => setSection(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="save-area">
          {dirty && <span className="dot dirty" title="有未保存修改" />}
          {savedAt && !dirty && <span className="saved-hint">已保存 {savedAt}</span>}
          <button className="btn primary" disabled={!dirty} onClick={() => void save()}>
            保存模型
          </button>
          <button
            className="btn"
            onClick={() => setDraft(JSON.parse(JSON.stringify(model)))}
          >
            撤销修改
          </button>
        </div>
      </div>
      {saveError && <div className="banner error">保存失败：{saveError}</div>}

      {section === "tables" && <TablesSection model={draft} />}
      {section === "relations" && (
        <RelationsSection model={draft} patch={patch} />
      )}
      {section === "dimensions" && (
        <DimensionsSection model={draft} patch={patch} />
      )}
      {section === "measures" && <MeasuresSection model={draft} patch={patch} />}
      {section === "calc" && <CalcSection model={draft} patch={patch} />}
    </div>
  );
}

/* ---------------- 表（只读自省视图） ---------------- */
function TablesSection({ model }: { model: ModelSpec }) {
  const [open, setOpen] = useState<string | null>(model.tables[0]?.name ?? null);
  return (
    <div className="card-list">
      <p className="section-hint">
        物理表来自数据库自省；建模界面不提供建表能力，只在其上声明语义。
      </p>
      {model.tables.map((t) => (
        <div key={t.name} className="card">
          <header onClick={() => setOpen(open === t.name ? null : t.name)}>
            <strong>{t.label}</strong>
            <code>
              {t.schema_name}.{t.name}
            </code>
            <span className="muted">{t.columns.length} 列 · 主键 {t.primary_key}</span>
          </header>
          {open === t.name && (
            <table className="mini-table">
              <thead>
                <tr><th>列名</th><th>类型</th><th>说明</th></tr>
              </thead>
              <tbody>
                {t.columns.map((c) => (
                  <tr key={c.name}>
                    <td><code>{c.name}</code>{c.name === t.primary_key && " 🔑"}</td>
                    <td>{c.data_type}</td>
                    <td className="muted">{c.label ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ))}
    </div>
  );
}

/* ---------------- 关联 ---------------- */
function RelationsSection({
  model,
  patch,
}: {
  model: ModelSpec;
  patch: (fn: (m: ModelSpec) => ModelSpec) => void;
}) {
  const update = (id: string, changes: Partial<RelationSpec>) =>
    patch((m) => ({
      ...m,
      relations: m.relations.map((r) => (r.id === id ? { ...r, ...changes } : r)),
    }));
  const remove = (id: string) =>
    patch((m) => ({ ...m, relations: m.relations.filter((r) => r.id !== id) }));
  const add = () =>
    patch((m) => ({
      ...m,
      relations: [
        ...m.relations,
        {
          id: `rel_${Date.now()}`,
          label: "新关联",
          left_table: m.tables[0]?.name ?? "",
          left_column: "",
          right_table: m.tables[1]?.name ?? m.tables[0]?.name ?? "",
          right_column: "",
          cardinality: "N:1" as Cardinality,
        },
      ],
    }));

  return (
    <div className="card-list">
      <p className="section-hint">
        指明两张表通过哪对字段连接，以及从左表看向右表的基数。
        一对多（1:N / N:1）下的重复计数由 SQL 内核自动用去重派生表消除。
      </p>
      {model.relations.map((r) => (
        <RelationEditor
          key={r.id}
          model={model}
          relation={r}
          onChange={(c) => update(r.id, c)}
          onRemove={() => remove(r.id)}
        />
      ))}
      <button className="btn" onClick={add}>＋ 新增关联</button>
    </div>
  );
}

function RelationEditor({
  model,
  relation,
  onChange,
  onRemove,
}: {
  model: ModelSpec;
  relation: RelationSpec;
  onChange: (c: Partial<RelationSpec>) => void;
  onRemove: () => void;
}) {
  const colsOf = (t: string) =>
    model.tables.find((x) => x.name === t)?.columns ?? [];
  return (
    <div className="card relation-card">
      <input
        className="title-input"
        value={relation.label}
        onChange={(e) => onChange({ label: e.target.value })}
      />
      <div className="relation-grid">
        <select
          value={relation.left_table}
          onChange={(e) => onChange({ left_table: e.target.value, left_column: "" })}
        >
          {model.tables.map((t) => <option key={t.name} value={t.name}>{t.label}</option>)}
        </select>
        <select
          value={relation.left_column}
          onChange={(e) => onChange({ left_column: e.target.value })}
        >
          <option value="">字段…</option>
          {colsOf(relation.left_table).map((c) => (
            <option key={c.name} value={c.name}>{c.name}</option>
          ))}
        </select>
        <select
          value={relation.cardinality}
          onChange={(e) => onChange({ cardinality: e.target.value as Cardinality })}
        >
          {["1:1", "1:N", "N:1", "N:N"].map((x) => <option key={x}>{x}</option>)}
        </select>
        <select
          value={relation.right_table}
          onChange={(e) => onChange({ right_table: e.target.value, right_column: "" })}
        >
          {model.tables.map((t) => <option key={t.name} value={t.name}>{t.label}</option>)}
        </select>
        <select
          value={relation.right_column}
          onChange={(e) => onChange({ right_column: e.target.value })}
        >
          <option value="">字段…</option>
          {colsOf(relation.right_table).map((c) => (
            <option key={c.name} value={c.name}>{c.name}</option>
          ))}
        </select>
        <button className="btn danger" onClick={onRemove}>删除</button>
      </div>
    </div>
  );
}

/* ---------------- 维度与层级 ---------------- */
function DimensionsSection({
  model,
  patch,
}: {
  model: ModelSpec;
  patch: (fn: (m: ModelSpec) => ModelSpec) => void;
}) {
  const updateDim = (id: string, c: Partial<DimensionSpec>) =>
    patch((m) => ({
      ...m,
      dimensions: m.dimensions.map((d) => (d.id === id ? { ...d, ...c } : d)),
    }));
  const removeDim = (id: string) =>
    patch((m) => ({
      ...m,
      dimensions: m.dimensions.filter((d) => d.id !== id),
      hierarchies: m.hierarchies.map((h) => ({
        ...h,
        dimension_ids: h.dimension_ids.filter((x) => x !== id),
      })),
    }));
  const addDim = () =>
    patch((m) => ({
      ...m,
      dimensions: [
        ...m.dimensions,
        {
          id: `dim_${Date.now()}`,
          name: "新维度",
          table: m.tables[0]?.name ?? "",
          column: m.tables[0]?.columns[0]?.name,
          expression: null,
          data_type: "text" as DataType,
          hierarchy_id: null,
          level_index: null,
        },
      ],
    }));
  const updateHier = (id: string, dimension_ids: string[]) =>
    patch((m) => ({
      ...m,
      hierarchies: m.hierarchies.map((h) =>
        h.id === id ? { ...h, dimension_ids } : h
      ),
      // 同步维度上的 hierarchy_id / level_index
      dimensions: m.dimensions.map((d) => {
        const idx = dimension_ids.indexOf(d.id);
        return idx >= 0
          ? { ...d, hierarchy_id: id, level_index: idx }
          : d.hierarchy_id === id
          ? { ...d, hierarchy_id: null, level_index: null }
          : d;
      }),
    }));
  const renameHier = (id: string, name: string) =>
    patch((m) => ({
      ...m,
      hierarchies: m.hierarchies.map((h) => (h.id === id ? { ...h, name } : h)),
    }));
  const addHier = () =>
    patch((m) => ({
      ...m,
      hierarchies: [
        ...m.hierarchies,
        { id: `h_${Date.now()}`, name: "新层级", dimension_ids: [] },
      ],
    }));
  const removeHier = (id: string) =>
    patch((m) => ({
      ...m,
      hierarchies: m.hierarchies.filter((h) => h.id !== id),
      dimensions: m.dimensions.map((d) =>
        d.hierarchy_id === id
          ? { ...d, hierarchy_id: null, level_index: null }
          : d
      ),
    }));

  return (
    <div className="card-list">
      <p className="section-hint">
        维度是可直接分组的分类字段。把维度按"粗 → 细"顺序排进层级，
        查询时点击聚合值就会沿该路径下钻。
      </p>
      <div className="grid-2">
        <div>
          <h4>维度</h4>
          {model.dimensions.map((d) => (
            <DimEditor
              key={d.id}
              model={model}
              dim={d}
              onChange={(c) => updateDim(d.id, c)}
              onRemove={() => removeDim(d.id)}
            />
          ))}
          <button className="btn" onClick={addDim}>＋ 新增维度</button>
        </div>
        <div>
          <h4>层级（自上而下）</h4>
          {model.hierarchies.map((h) => (
            <HierarchyEditor
              key={h.id}
              model={model}
              hierarchy={h}
              onChange={(ids) => updateHier(h.id, ids)}
              onRename={(name) => renameHier(h.id, name)}
              onRemove={() => removeHier(h.id)}
            />
          ))}
          <button className="btn" onClick={addHier}>＋ 新增层级</button>
        </div>
      </div>
    </div>
  );
}

function DimEditor({
  model,
  dim,
  onChange,
  onRemove,
}: {
  model: ModelSpec;
  dim: DimensionSpec;
  onChange: (c: Partial<DimensionSpec>) => void;
  onRemove: () => void;
}) {
  const table = model.tables.find((t) => t.name === dim.table);
  return (
    <div className="card compact">
      <div className="row">
        <input
          value={dim.name}
          onChange={(e) => onChange({ name: e.target.value })}
        />
        <select
          value={dim.table}
          onChange={(e) => {
            const first = model.tables.find((t) => t.name === e.target.value)
              ?.columns[0]?.name;
            onChange({ table: e.target.value, column: first, expression: null });
          }}
        >
          {model.tables.map((t) => <option key={t.name} value={t.name}>{t.label}</option>)}
        </select>
        <select
          value={dim.data_type}
          onChange={(e) => onChange({ data_type: e.target.value as DataType })}
        >
          {["text", "number", "integer", "date", "boolean"].map((x) => (
            <option key={x}>{x}</option>
          ))}
        </select>
        <button className="btn danger small" onClick={onRemove}>×</button>
      </div>
      <div className="row">
        <select
          value={dim.expression ? "__expr__" : dim.column ?? ""}
          onChange={(e) =>
            e.target.value === "__expr__"
              ? onChange({ column: null, expression: dim.expression ?? "" })
              : onChange({ column: e.target.value, expression: null })
          }
        >
          {table?.columns.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
          <option value="__expr__">表达式…</option>
        </select>
        {dim.expression !== null && (
          <input
            className="expr-input"
            placeholder="如 DATE_TRUNC('month', order_date)"
            value={dim.expression ?? ""}
            onChange={(e) => onChange({ expression: e.target.value })}
          />
        )}
        <code className="muted">{dim.id}</code>
      </div>
    </div>
  );
}

function HierarchyEditor({
  model,
  hierarchy,
  onChange,
  onRename,
  onRemove,
}: {
  model: ModelSpec;
  hierarchy: HierarchySpec;
  onChange: (ids: string[]) => void;
  onRename: (name: string) => void;
  onRemove: () => void;
}) {
  const move = (i: number, delta: number) => {
    const ids = [...hierarchy.dimension_ids];
    const j = i + delta;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    onChange(ids);
  };
  const used = new Set(hierarchy.dimension_ids);
  return (
    <div className="card compact">
      <div className="row">
        <input
          value={hierarchy.name}
          onChange={(e) => onRename(e.target.value)}
        />
        <button className="btn danger small" onClick={onRemove}>删除</button>
      </div>
      <ol className="level-list">
        {hierarchy.dimension_ids.map((id, i) => {
          const d = model.dimensions.find((x) => x.id === id);
          return (
            <li key={id}>
              <span className="level-no">L{i + 1}</span>
              {d?.name ?? id}
              <button className="btn small" onClick={() => move(i, -1)}>↑</button>
              <button className="btn small" onClick={() => move(i, 1)}>↓</button>
              <button
                className="btn small"
                onClick={() => onChange(hierarchy.dimension_ids.filter((x) => x !== id))}
              >
                移出
              </button>
            </li>
          );
        })}
      </ol>
      <select
        value=""
        onChange={(e) => e.target.value && onChange([...hierarchy.dimension_ids, e.target.value])}
      >
        <option value="">＋ 加入维度…</option>
        {model.dimensions
          .filter((d) => !used.has(d.id))
          .map((d) => (
            <option key={d.id} value={d.id}>{d.name}</option>
          ))}
      </select>
    </div>
  );
}

/* ---------------- 度量 ---------------- */
function MeasuresSection({
  model,
  patch,
}: {
  model: ModelSpec;
  patch: (fn: (m: ModelSpec) => ModelSpec) => void;
}) {
  const update = (id: string, c: Partial<MeasureSpec>) =>
    patch((m) => ({
      ...m,
      measures: m.measures.map((x) => (x.id === id ? { ...x, ...c } : x)),
    }));
  const remove = (id: string) =>
    patch((m) => ({ ...m, measures: m.measures.filter((x) => x.id !== id) }));
  const add = () =>
    patch((m) => ({
      ...m,
      measures: [
        ...m.measures,
        {
          id: `m_${Date.now()}`,
          name: "新度量",
          table: m.tables[0]?.name ?? "",
          agg: "sum" as AggKind,
          column: m.tables[0]?.columns.find((c) =>
            ["number", "integer"].includes(c.data_type)
          )?.name,
          expression: null,
          filter: null,
        },
      ],
    }));

  return (
    <div className="card-list">
      <p className="section-hint">
        在数值字段上施加聚合；可选用沙箱表达式作为度量基，并可附一个过滤条件
        （生成 FILTER (WHERE …)）。
      </p>
      {model.measures.map((m) => (
        <MeasureEditor
          key={m.id}
          model={model}
          measure={m}
          onChange={(c) => update(m.id, c)}
          onRemove={() => remove(m.id)}
        />
      ))}
      <button className="btn" onClick={add}>＋ 新增度量</button>
    </div>
  );
}

function MeasureEditor({
  model,
  measure,
  onChange,
  onRemove,
}: {
  model: ModelSpec;
  measure: MeasureSpec;
  onChange: (c: Partial<MeasureSpec>) => void;
  onRemove: () => void;
}) {
  const table = model.tables.find((t) => t.name === measure.table);
  const numericCols = (table?.columns ?? []).filter((c) =>
    ["number", "integer"].includes(c.data_type)
  );
  const needColumn = measure.agg !== "count";
  return (
    <div className="card compact">
      <div className="row">
        <input
          value={measure.name}
          onChange={(e) => onChange({ name: e.target.value })}
        />
        <select
          value={measure.table}
          onChange={(e) => {
            const first = model.tables
              .find((t) => t.name === e.target.value)
              ?.columns.find((c) => ["number", "integer"].includes(c.data_type))?.name;
            onChange({ table: e.target.value, column: first, expression: null });
          }}
        >
          {model.tables.map((t) => <option key={t.name} value={t.name}>{t.label}</option>)}
        </select>
        <select
          value={measure.agg}
          onChange={(e) => onChange({ agg: e.target.value as AggKind })}
        >
          {(["sum", "count", "count_distinct", "avg", "min", "max"] as AggKind[]).map(
            (x) => <option key={x} value={x}>{x}</option>
          )}
        </select>
        <button className="btn danger small" onClick={onRemove}>×</button>
      </div>
      {needColumn && (
        <div className="row">
          <select
            value={measure.expression ? "__expr__" : measure.column ?? ""}
            onChange={(e) =>
              e.target.value === "__expr__"
                ? onChange({ column: null, expression: measure.expression ?? "" })
                : onChange({ column: e.target.value, expression: null })
            }
          >
            {numericCols.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
            <option value="__expr__">表达式…</option>
          </select>
          {measure.expression !== null && (
            <input
              className="expr-input"
              placeholder="如 quantity * unit_price"
              value={measure.expression}
              onChange={(e) => onChange({ expression: e.target.value })}
            />
          )}
        </div>
      )}
      <div className="row">
        <input
          className="expr-input"
          placeholder="可选过滤表达式，如 status = 'paid'"
          value={measure.filter ?? ""}
          onChange={(e) => onChange({ filter: e.target.value || null })}
        />
      </div>
    </div>
  );
}

/* ---------------- 计算字段 ---------------- */
function CalcSection({
  model,
  patch,
}: {
  model: ModelSpec;
  patch: (fn: (m: ModelSpec) => ModelSpec) => void;
}) {
  const update = (id: string, c: Partial<CalculatedFieldSpec>) =>
    patch((m) => ({
      ...m,
      calculated_fields: m.calculated_fields.map((x) =>
        x.id === id ? { ...x, ...c } : x
      ),
    }));
  const remove = (id: string) =>
    patch((m) => ({
      ...m,
      calculated_fields: m.calculated_fields.filter((x) => x.id !== id),
    }));
  const add = () =>
    patch((m) => ({
      ...m,
      calculated_fields: [
        ...m.calculated_fields,
        {
          id: `cf_${Date.now()}`,
          name: "新计算字段",
          table: m.tables[0]?.name ?? "",
          expression: "",
          data_type: "number" as DataType,
          join_tables: [],
        },
      ],
    }));

  return (
    <div className="card-list">
      <p className="section-hint">
        表达式只允许白名单函数（IF、ROUND、UPPER、CONCAT、DATE_TRUNC 等），
        所有字面量参数化。失焦即校验，错误会定位到行列区间。
      </p>
      {model.calculated_fields.map((c) => (
        <CalcEditor
          key={c.id}
          model={model}
          calc={c}
          onChange={(ch) => update(c.id, ch)}
          onRemove={() => remove(c.id)}
        />
      ))}
      <button className="btn" onClick={add}>＋ 新增计算字段</button>
    </div>
  );
}

function CalcEditor({
  model,
  calc,
  onChange,
  onRemove,
}: {
  model: ModelSpec;
  calc: CalculatedFieldSpec;
  onChange: (c: Partial<CalculatedFieldSpec>) => void;
  onRemove: () => void;
}) {
  const [validation, setValidation] = useState<Awaited<
    ReturnType<typeof api.validateExpression>
  > | null>(null);
  const [checking, setChecking] = useState(false);

  const validate = async () => {
    if (!calc.expression.trim()) {
      setValidation(null);
      return;
    }
    setChecking(true);
    try {
      setValidation(
        await api.validateExpression(
          calc.table,
          calc.expression,
          calc.join_tables ?? []
        )
      );
    } catch (e) {
      setValidation({ valid: false, error: { message: (e as Error).message, line: 1, col: 0 } });
    } finally {
      setChecking(false);
    }
  };

  // 根据错误列位置高亮表达式文本（单行时用 start/end 切片）
  const err = validation?.valid === false ? validation.error : null;

  return (
    <div className="card compact">
      <div className="row">
        <input
          value={calc.name}
          onChange={(e) => onChange({ name: e.target.value })}
        />
        <select
          value={calc.table}
          onChange={(e) => onChange({ table: e.target.value, join_tables: [] })}
        >
          {model.tables.map((t) => <option key={t.name} value={t.name}>{t.label}</option>)}
        </select>
        <select
          value={calc.data_type}
          onChange={(e) => onChange({ data_type: e.target.value as DataType })}
        >
          {["number", "integer", "text", "date", "boolean"].map((x) => (
            <option key={x}>{x}</option>
          ))}
        </select>
        <button className="btn danger small" onClick={onRemove}>×</button>
      </div>
      <textarea
        className={`expr-area ${err ? "has-error" : ""} ${
          validation?.valid ? "ok" : ""
        }`}
        rows={2}
        placeholder="如 IF(unit_price * quantity > 1000, '大单', '普通')"
        value={calc.expression}
        onChange={(e) => onChange({ expression: e.target.value })}
        onBlur={() => void validate()}
      />
      {checking && <div className="muted small">校验中…</div>}
      {validation?.valid && (
        <div className="ok-hint small">
          ✓ 表达式合法；引用表：{validation.tables?.join("、")}
        </div>
      )}
      {err && (
        <div className="err-hint small">
          第 {err.line} 行 第 {err.col + 1} 列：{err.message}
          {err.end_col != null && err.line === 1 && (
            <div className="err-ruler">
              <pre>
                {calc.expression.slice(0, err.col)}
                <mark>
                  {calc.expression.slice(err.col, err.end_col ?? err.col + 1) ||
                    " "}
                </mark>
                {calc.expression.slice(err.end_col ?? err.col + 1)}
              </pre>
            </div>
          )}
        </div>
      )}
      <div className="row">
        <label className="muted small">允许连接的额外表（多对一可达）：</label>
        <div className="join-tables">
          {model.tables
            .filter((t) => t.name !== calc.table)
            .map((t) => (
              <label key={t.name} className="check">
                <input
                  type="checkbox"
                  checked={(calc.join_tables ?? []).includes(t.name)}
                  onChange={(e) => {
                    const set = new Set(calc.join_tables ?? []);
                    e.target.checked ? set.add(t.name) : set.delete(t.name);
                    onChange({ join_tables: [...set] });
                  }}
                />
                {t.label}
              </label>
            ))}
        </div>
      </div>
    </div>
  );
}
