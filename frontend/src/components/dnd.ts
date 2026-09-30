import type { DragItem } from "../types";

/** 统一的 HTML5 拖拽载体，杜绝任何自由文本拼接。 */
export function startDrag(e: React.DragEvent, item: DragItem) {
  e.dataTransfer.setData("application/x-dragquery", JSON.stringify(item));
  e.dataTransfer.effectAllowed = "copy";
}

export function readDrag(e: React.DragEvent): DragItem | null {
  const raw = e.dataTransfer.getData("application/x-dragquery");
  if (!raw) return null;
  try {
    const item = JSON.parse(raw) as DragItem;
    if (
      (item.kind === "dimension" ||
        item.kind === "measure" ||
        item.kind === "calculated") &&
      typeof item.id === "string"
    ) {
      return item;
    }
  } catch {
    /* ignore */
  }
  return null;
}
