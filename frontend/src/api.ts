import type {
  ExpressionValidation,
  ModelSpec,
  QueryResponse,
  QuerySpec,
} from "./types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const body = await resp.json();
      detail =
        typeof body.detail === "string"
          ? body.detail
          : JSON.stringify(body.detail ?? body);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  getModel: () => request<ModelSpec>("/api/model"),
  saveModel: (model: ModelSpec) =>
    request<{ ok: boolean }>("/api/model", {
      method: "PUT",
      body: JSON.stringify({ model }),
    }),
  runQuery: (spec: QuerySpec) =>
    request<QueryResponse>("/api/query", {
      method: "POST",
      body: JSON.stringify(spec),
    }),
  explainQuery: (spec: QuerySpec) =>
    request<QueryResponse>("/api/query/explain", {
      method: "POST",
      body: JSON.stringify(spec),
    }),
  validateExpression: (table: string, expression: string, joinTables: string[]) =>
    request<ExpressionValidation>("/api/validate-expression", {
      method: "POST",
      body: JSON.stringify({
        table,
        expression,
        join_tables: joinTables,
      }),
    }),
  dimensionValues: (dimId: string) =>
    request<{ values: (string | number)[] }>(
      `/api/dimensions/${encodeURIComponent(dimId)}/values`
    ),
};
