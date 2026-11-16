import { HealthStatus } from "@/app/components/health-status";

export default function Home() {
  return (
    <main className="relative min-h-screen overflow-hidden">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_68%_20%,rgba(34,211,238,0.12),transparent_32%),radial-gradient(circle_at_20%_80%,rgba(99,102,241,0.1),transparent_30%)]" />

      <div className="relative mx-auto flex min-h-screen w-full max-w-6xl flex-col px-6 py-8 sm:px-10 lg:px-12">
        <header className="flex items-center justify-between border-b border-white/10 pb-6">
          <div className="flex items-center gap-3">
            <span className="grid h-9 w-9 place-items-center rounded-xl border border-cyan-400/30 bg-cyan-400/10 text-sm font-semibold text-cyan-300">
              PP
            </span>
            <div>
              <p className="text-sm font-semibold tracking-wide text-slate-100">
                PaperPilot
              </p>
              <p className="text-xs text-slate-500">ResearchOps Platform</p>
            </div>
          </div>
          <span className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5 font-mono text-xs text-slate-400">
            phase 01
          </span>
        </header>

        <div className="grid flex-1 items-center gap-14 py-16 lg:grid-cols-[1.25fr_0.75fr] lg:py-20">
          <section>
            <p className="mb-6 inline-flex items-center gap-2 rounded-full border border-cyan-400/20 bg-cyan-400/[0.06] px-3 py-1.5 text-xs font-medium text-cyan-200">
              <span className="h-1.5 w-1.5 rounded-full bg-cyan-300" />
              AI-native research infrastructure
            </p>
            <h1 className="max-w-3xl text-4xl font-semibold leading-[1.08] tracking-[-0.04em] text-white sm:text-6xl">
              PaperPilot
              <span className="block bg-gradient-to-r from-cyan-300 to-indigo-300 bg-clip-text text-transparent">
                ResearchOps
              </span>
            </h1>
            <p className="mt-6 text-xl font-medium tracking-wide text-slate-300">
              AI 科研工作流与实验智能管理平台
            </p>
            <p className="mt-5 max-w-2xl text-base leading-8 text-slate-400 sm:text-lg">
              将文献理解、可信引用、研究智能体与机器学习实验管理整合到同一条可追踪、可复现的科研工作流中。
            </p>

            <div className="mt-10 grid max-w-2xl gap-px overflow-hidden rounded-2xl border border-white/10 bg-white/10 sm:grid-cols-3">
              {[
                ["01", "Literature intelligence"],
                ["02", "Experiment tracking"],
                ["03", "Reproducible research"],
              ].map(([number, label]) => (
                <div key={number} className="bg-slate-950/90 p-4">
                  <p className="font-mono text-xs text-cyan-400/70">{number}</p>
                  <p className="mt-2 text-sm text-slate-300">{label}</p>
                </div>
              ))}
            </div>
          </section>

          <aside className="lg:pl-8">
            <HealthStatus />
            <p className="mt-4 px-2 text-xs leading-5 text-slate-600">
              状态由浏览器实时请求 FastAPI 健康检查接口生成，并非模拟数据。
            </p>
          </aside>
        </div>

        <footer className="flex flex-col gap-2 border-t border-white/10 pt-5 text-xs text-slate-600 sm:flex-row sm:items-center sm:justify-between">
          <p>Built for rigorous, reproducible research.</p>
          <p className="font-mono">Next.js · FastAPI · TypeScript · Python</p>
        </footer>
      </div>
    </main>
  );
}
