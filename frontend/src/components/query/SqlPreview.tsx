import { useState } from "react";
import type { QueryResponse } from "../../types";

export default function SqlPreview({ result }: { result: QueryResponse }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="sql-preview">
      <button className="link" onClick={() => setOpen((v) => !v)}>
        {open ? "隐藏生成的 SQL" : "查看生成的 SQL 与参数"}
      </button>
      {open && (
        <div className="sql-body">
          <pre className="sql-text">{result.sql}</pre>
          <div className="sql-meta">
            <div>
              <strong>JOIN 路径：</strong>
              {result.joined_tables.join(" → ") || "（单表，无 JOIN）"}
            </div>
            {result.fanout_tables.length > 0 && (
              <div className="warn">
                <strong>一对多放大保护：</strong>
                {result.fanout_tables.join("、")} 的度量走按主键去重派生表聚合
              </div>
            )}
            <div>
              <strong>绑定参数：</strong>
              <code>
                [{result.params
                  .map((p) =>
                    typeof p === "string" ? JSON.stringify(p) : String(p)
                  )
                  .join(", ")}]
              </code>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
