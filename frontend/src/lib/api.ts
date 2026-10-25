/** API client for the Foot8 backend. */

import type { BacktestSummary, Dashboard, HistoryResponse } from "@/types/api";

export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8100/api/v1";

async function fetchJSON<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "Unknown error");
    throw new Error(`API Error ${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  getDashboard: (days = 7) => fetchJSON<Dashboard>(`/dashboard?days=${days}`),
  refresh: (days = 7) => fetchJSON<{ refreshed: string[] }>(`/refresh?days=${days}`, { method: "POST" }),
  getBacktest: () => fetchJSON<BacktestSummary>("/backtest"),
  getHistory: (days = 7, force = false) =>
    fetchJSON<HistoryResponse>(`/history?days=${days}${force ? "&force=true" : ""}`),
};
