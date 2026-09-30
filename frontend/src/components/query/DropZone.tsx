import { useState } from "react";
import type { DragItem, ModelSpec } from "../../types";
import { readDrag } from "../dnd";

interface Props {
  zone: "rows" | "columns" | "measures";
  title: string;
  subtitle: string;
  ids: string[];
  model: ModelSpec;
  onDrop: (item: DragItem) => void;
  onRemove: (id: string) => void;
  renderBadge?: (id: string) => React.ReactNode;
}

export default function DropZone({
  zone,
  title,
  subtitle,
  ids,
  model,
  onDrop,
  onRemove,
  renderBadge,
}: Props) {
  const [over, setOver] = useState(false);

  const nameOf = (id: string) =>
    model.measures.find((m) => m.id === id)?.name ??
    model.dimensions.find((d) => d.id === id)?.name ??
    model.calculated_fields.find((c) => c.id === id)?.name ??
    id;

  return (
    <div
      className={`drop-zone ${over ? "over" : ""} ${
        ids.length ? "filled" : ""
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
        if (item) onDrop(item);
      }}
    >
      <div className="drop-head">
        <strong>{title}</strong>
        <span className="drop-sub">{subtitle}</span>
      </div>
      <div className="drop-body">
        {ids.length === 0 && (
          <span className="drop-placeholder">把字段拖到这里</span>
        )}
        {ids.map((id) => (
          <span
            key={id}
            className={`pill ${zone === "measures" ? "pill-measure" : "pill-dim"}`}
          >
            {nameOf(id)}
            {renderBadge?.(id)}
            <button
              className="pill-x"
              title="移除"
              onClick={() => onRemove(id)}
            >
              ×
            </button>
          </span>
        ))}
      </div>
    </div>
  );
}
