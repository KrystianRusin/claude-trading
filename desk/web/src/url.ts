// Page state that belongs in the URL, so any view can be shared as a link.
import { useSearchParams } from "react-router-dom";

export function useParam<T extends string>(key: string, fallback: T, allowed?: readonly T[]): [T, (v: T) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get(key) as T | null;
  const value = raw !== null && (!allowed || allowed.includes(raw)) ? raw : fallback;
  const set = (v: T) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    if (v === fallback) next.delete(key); else next.set(key, v);
    return next;
  }, { replace: true });
  return [value, set];
}

// Set several parameters at once (null removes one), e.g. a new bot and no selected day.
export function useSetParams() {
  const [, setParams] = useSearchParams();
  return (changes: Record<string, string | null>) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    for (const [k, v] of Object.entries(changes)) if (v === null || v === "") next.delete(k); else next.set(k, v);
    return next;
  }, { replace: true });
}
