import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./client";

type ApiState<T> = {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
};

// Minimal GET hook. Pass `null` path to skip. `pollMs` re-fetches on an interval
// (used by the HITL queue / live chat as the realtime fallback). `deps` re-runs
// when query inputs change.
export function useApi<T>(
  path: string | null,
  opts: { pollMs?: number; deps?: ReadonlyArray<unknown> } = {},
): ApiState<T> {
  const { pollMs, deps = [] } = opts;
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState<boolean>(Boolean(path));
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);

  const reload = useCallback(() => {
    if (!path) {
      setData(null);
      setLoading(false);
      return;
    }
    api
      .get<T>(path)
      .then((d) => {
        if (!mounted.current) return;
        setData(d);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!mounted.current) return;
        setError(e instanceof ApiError ? e.message : "Lỗi tải dữ liệu");
      })
      .finally(() => mounted.current && setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);

  useEffect(() => {
    mounted.current = true;
    setLoading(Boolean(path));
    reload();
    let timer: ReturnType<typeof setInterval> | undefined;
    if (path && pollMs) timer = setInterval(reload, pollMs);
    return () => {
      mounted.current = false;
      if (timer) clearInterval(timer);
    };
  }, [reload, pollMs, path]);

  return { data, loading, error, reload };
}
