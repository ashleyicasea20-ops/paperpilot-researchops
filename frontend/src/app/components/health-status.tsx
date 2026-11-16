"use client";

import { useEffect, useState } from "react";

type HealthPayload = {
  status: string;
  service: string;
  version: string;
};

type ConnectionState =
  | { kind: "connecting" }
  | { kind: "connected"; health: HealthPayload }
  | { kind: "failed"; message: string };

const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export function HealthStatus() {
  const [attempt, setAttempt] = useState(0);
  const [connection, setConnection] = useState<ConnectionState>({
    kind: "connecting",
  });

  useEffect(() => {
    const controller = new AbortController();

    async function checkHealth() {
      setConnection({ kind: "connecting" });

      try {
        const response = await fetch(`${apiBaseUrl}/api/v1/health`, {
          cache: "no-store",
          headers: { Accept: "application/json" },
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const health = (await response.json()) as HealthPayload;
        if (health.status !== "ok" || health.service !== "paperpilot-api") {
          throw new Error("Unexpected health response");
        }

        setConnection({ kind: "connected", health });
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }

        setConnection({
          kind: "failed",
          message: error instanceof Error ? error.message : "Unknown error",
        });
      }
    }

    void checkHealth();
    return () => controller.abort();
  }, [attempt]);

  const isConnecting = connection.kind === "connecting";
  const isConnected = connection.kind === "connected";

  return (
    <section
      className="rounded-2xl border border-white/10 bg-slate-950/70 p-5 shadow-2xl shadow-cyan-950/20 backdrop-blur"
      aria-labelledby="api-status-title"
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
            System status
          </p>
          <h2
            id="api-status-title"
            className="mt-2 text-base font-medium text-slate-100"
          >
            FastAPI backend
          </h2>
        </div>
        <span
          className={`mt-1 h-2.5 w-2.5 rounded-full ${
            isConnecting
              ? "animate-pulse bg-amber-400"
              : isConnected
                ? "bg-emerald-400 shadow-[0_0_16px_rgba(52,211,153,0.8)]"
                : "bg-rose-400"
          }`}
          aria-hidden="true"
        />
      </div>

      <div className="mt-5 flex items-end justify-between gap-4">
        <div aria-live="polite">
          <p
            className={`text-sm font-medium ${
              isConnecting
                ? "text-amber-300"
                : isConnected
                  ? "text-emerald-300"
                  : "text-rose-300"
            }`}
          >
            {isConnecting
              ? "正在连接"
              : isConnected
                ? "连接成功"
                : "连接失败"}
          </p>
          <p className="mt-1 font-mono text-xs text-slate-500">
            {isConnected
              ? `${connection.health.service} · v${connection.health.version}`
              : connection.kind === "failed"
                ? connection.message
                : `${apiBaseUrl}/api/v1/health`}
          </p>
        </div>

        {connection.kind === "failed" ? (
          <button
            type="button"
            onClick={() => setAttempt((current) => current + 1)}
            className="shrink-0 rounded-lg border border-white/10 px-3 py-2 text-xs font-medium text-slate-300 transition hover:border-cyan-400/40 hover:text-cyan-200 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-400"
          >
            重新连接
          </button>
        ) : null}
      </div>
    </section>
  );
}
