import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { api } from "../api";
import type { ModelSpec } from "../types";

interface ModelContextValue {
  model: ModelSpec | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
  save: (model: ModelSpec) => Promise<void>;
}

const ModelContext = createContext<ModelContextValue | null>(null);

export function ModelProvider({ children }: { children: ReactNode }) {
  const [model, setModel] = useState<ModelSpec | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setModel(await api.getModel());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  const save = useCallback(async (next: ModelSpec) => {
    await api.saveModel(next);
    setModel(next);
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <ModelContext.Provider value={{ model, loading, error, reload, save }}>
      {children}
    </ModelContext.Provider>
  );
}

export function useModel(): ModelContextValue {
  const ctx = useContext(ModelContext);
  if (!ctx) throw new Error("useModel 必须在 ModelProvider 内使用");
  return ctx;
}
