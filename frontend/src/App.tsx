import { useState } from "react";
import { useModel } from "./state/model";
import QueryPanel from "./components/query/QueryPanel";
import ModelerPanel from "./components/model/ModelerPanel";

type Tab = "query" | "model";

export default function App() {
  const { model, loading, error, reload } = useModel();
  const [tab, setTab] = useState<Tab>("query");
  const [modelVersion, setModelVersion] = useState(0);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">▦</span>
          <div>
            <h1>DragQuery</h1>
            <p>拖拽即生成查询 · 自助数据查询</p>
          </div>
        </div>
        <nav className="tabs">
          <button
            className={tab === "query" ? "tab active" : "tab"}
            onClick={() => setTab("query")}
          >
            查询构建
          </button>
          <button
            className={tab === "model" ? "tab active" : "tab"}
            onClick={() => setTab("model")}
          >
            数据建模
          </button>
          <button className="tab ghost" onClick={() => void reload()}>
            重新加载模型
          </button>
        </nav>
      </header>

      {loading && <div className="banner info">正在加载语义模型…</div>}
      {error && <div className="banner error">模型加载失败：{error}</div>}

      <main>
        {model && tab === "query" && (
          <QueryPanel model={model} modelVersion={modelVersion} />
        )}
        {model && tab === "model" && (
          <ModelerPanel
            model={model}
            onSaved={() => setModelVersion((v) => v + 1)}
          />
        )}
      </main>
    </div>
  );
}
