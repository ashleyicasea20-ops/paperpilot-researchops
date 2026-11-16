# PaperPilot ResearchOps

PaperPilot ResearchOps 是面向 AI 科研工作流与实验智能管理的全栈平台。本仓库的第一阶段提供可运行的 Next.js 前端、FastAPI 后端、真实 HTTP 健康检查以及基础自动化测试。

## 技术栈

- Frontend: Next.js 16, React 19, TypeScript, Tailwind CSS 4, App Router
- Backend: Python 3.11, FastAPI, Uvicorn
- Testing: pytest, HTTPX / FastAPI TestClient

## 环境准备（Windows PowerShell）

需要安装：

- Python 3.11（可通过 `py -3.11` 调用）
- Node.js 24 与 npm 11
- Git

首次准备后端：

```powershell
Set-Location backend
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

如果 PowerShell 阻止激活脚本，可以不激活环境，直接使用 `.\.venv\Scripts\python.exe` 执行后端命令。

首次准备前端：

```powershell
Set-Location frontend
npm install
Copy-Item .env.example .env.local
```

`.env.local` 不应提交到 Git。默认值将浏览器请求指向 `http://localhost:8000`。

## 启动后端

在一个 PowerShell 终端中：

```powershell
Set-Location backend
.\.venv\Scripts\Activate.ps1
$env:CORS_ORIGINS = "http://localhost:3000"
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

健康检查地址：<http://localhost:8000/api/v1/health>

## 启动前端

在另一个 PowerShell 终端中：

```powershell
Set-Location frontend
Copy-Item .env.example .env.local -ErrorAction SilentlyContinue
npm run dev
```

访问 <http://localhost:3000>。页面会从 `NEXT_PUBLIC_API_BASE_URL` 指向的后端实时读取连接状态。

## 自动化测试与质量检查

后端测试：

```powershell
Set-Location backend
.\.venv\Scripts\Activate.ps1
python -m pytest -q
```

前端检查：

```powershell
Set-Location frontend
npm run lint
npm run build
```

## 项目目录结构

```text
paperpilot-researchops/
├── backend/
│   ├── .env.example
│   ├── .venv/                 # 本地虚拟环境，不提交
│   ├── app/
│   │   ├── __init__.py
│   │   └── main.py
│   ├── tests/
│   │   ├── __init__.py
│   │   └── test_health.py
│   └── requirements.txt
├── docs/
│   └── architecture.md
├── frontend/
│   ├── public/
│   ├── src/app/
│   │   ├── components/health-status.tsx
│   │   ├── globals.css
│   │   ├── layout.tsx
│   │   └── page.tsx
│   ├── .env.example
│   ├── package.json
│   └── package-lock.json
├── .gitignore
└── README.md
```

架构边界与后续扩展原则见 [`docs/architecture.md`](docs/architecture.md)。

## 当前阶段边界

第一阶段不包含 Docker、数据库、缓存、云部署、研究 Agent 编排或大模型 API。所有这些能力将在出现明确需求和数据边界后再选型。

## 本地验证记录

验证日期：2026-11-16（Windows PowerShell）

| 检查项 | 实际结果 |
| --- | --- |
| Python 虚拟环境 | Python 3.11.6，位于 `backend/.venv` |
| `python -m pytest -q` | 2 passed in 0.27s |
| `npm run lint` | 通过，0 errors |
| `npm run build` | 通过，Next.js 生成静态 `/` 与 `/_not-found` 路由 |
| `pip check` | `No broken requirements found.` |
| `npm audit --json` | 0 vulnerabilities |
| API 健康检查 | HTTP 200，响应为 `{"status":"ok","service":"paperpilot-api","version":"0.1.0"}` |
| CORS 预检 | HTTP 200，`Access-Control-Allow-Origin: http://localhost:3000` |
| 前端 HTTP | `http://localhost:3000` 返回 HTTP 200 |
| 浏览器 UI | 未完成。图形化浏览器工具缺少本机内核资源路径，Edge headless 也未返回可检查的 DOM；未将客户端“连接成功”状态记为已验证 |
