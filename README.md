# PaperPilot ResearchOps

PaperPilot ResearchOps 是面向 AI 科研工作流与实验智能管理的全栈平台。Stage 02A 提供 PostgreSQL 连接和数据库就绪检查；当前 Stage 02B 新增用户、工作空间与成员角色的 ORM 定义，尚未在 PostgreSQL 部署业务表。

## 技术栈

- Frontend: Next.js 16, React 19, TypeScript, Tailwind CSS 4, App Router
- Backend: Python 3.11, FastAPI, Uvicorn, SQLAlchemy 2.x, Psycopg 3
- Database: PostgreSQL 17
- Testing: pytest, HTTPX / FastAPI TestClient

## 环境准备（Windows PowerShell）

需要安装：

- Python 3.11（可通过 `py -3.11` 调用）
- Node.js 24 与 npm 11
- Git
- PostgreSQL 17（本机程序目录为 `D:\pgsql`，数据目录为 `D:\PostgreSQL\data\17`）

首次准备后端：

```powershell
Set-Location backend
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

如果 PowerShell 阻止激活脚本，可以不激活环境，直接使用 `.\.venv\Scripts\python.exe` 执行后端命令。

### PostgreSQL 与后端配置

检查 PostgreSQL 状态并启动本地 ZIP 版本：

```powershell
& "D:\pgsql\bin\pg_ctl.exe" status -D "D:\PostgreSQL\data\17"
& "D:\pgsql\bin\pg_ctl.exe" start -D "D:\PostgreSQL\data\17" -l "D:\PostgreSQL\data\17\postgresql.log"
& "D:\pgsql\bin\pg_isready.exe" -h 127.0.0.1 -p 5432 -d paperpilot_dev
```

首次配置后端时复制安全模板，并只在本机文件中填写真实密码：

```powershell
Set-Location backend
Copy-Item .env.example .env
# 使用编辑器填写 backend/.env 中的 DB_PASSWORD；不要把密码写入终端历史或提交到 Git。
```

`backend/.env` 使用 `DB_HOST`、`DB_PORT`、`DB_NAME`、`DB_USER` 和 `DB_PASSWORD`。程序通过 Pydantic Settings 加载配置，并使用 SQLAlchemy `URL.create` 构建连接 URL。根目录 `.gitignore` 已忽略真实 `.env` 文件。

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

API 检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/ready
```

- `/api/v1/health` 是进程健康检查，不访问数据库。
- `/api/v1/ready` 连接 PostgreSQL 并执行 `SELECT 1`；成功返回 HTTP 200，配置缺失或数据库不可用时返回 HTTP 503。

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
python -m pytest -q -m "not integration"
python -m pytest -q -m integration
python -m pip check
```

`integration` 标记的测试读取本机 `backend/.env` 并连接真实 PostgreSQL；缺少该文件时会明确跳过，不能用 Mock 结果替代。

模型单元测试不依赖 `.env`，只在启用外键检查的 SQLite 内存库中验证约束，并离线编译 PostgreSQL DDL。SQLite 与 PostgreSQL 的大小写、长度和时区行为存在差异，详细边界见架构文档。真实 PostgreSQL 测试仍只执行只读 `SELECT 1`。

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
│   │   ├── core/config.py
│   │   ├── db/base.py
│   │   ├── db/session.py
│   │   ├── models/             # User、Workspace、WorkspaceMember
│   │   └── main.py
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── test_config.py
│   │   ├── test_database_integration.py
│   │   ├── test_database_reporting.py
│   │   ├── test_health.py
│   │   ├── test_models.py
│   │   └── test_readiness.py
│   ├── pytest.ini
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

## 常见数据库错误

- `connection refused`：用 `pg_ctl status` 和 `pg_isready` 确认 PostgreSQL 已启动并监听 `127.0.0.1:5432`。
- `password authentication failed`：核对 `backend/.env` 中的用户和密码；不要把密码粘贴到日志、Issue 或聊天中。
- `/ready` 返回 503：先检查 `DB_PASSWORD` 是否已配置，再检查数据库 `paperpilot_dev` 与用户 `paperpilot_app` 是否存在并可登录。
- 连接超时：检查端口、防火墙和 `postgresql.conf` / `pg_hba.conf`。应用连接超时为 5 秒。

## 当前阶段边界

Stage 02B 在既有连接层上建立统一 Base.metadata、ORM 字段、关系、约束和离线测试，不创建真实业务表。所有权只由 Workspace.owner_id 表示，成员角色仅限 admin/editor/viewer；尚未实现认证或请求级授权。Stage 02C 才引入 Alembic 迁移。本阶段不引入 Docker、Redis、云部署、研究 Agent 编排或大模型 API。

## 本地验证记录

Stage 02B 验证日期：2026-11-19（Windows PowerShell）。

| 检查项 | 实际结果 |
| --- | --- |
| 模型测试（含 PostgreSQL DDL 离线编译） | 54 passed，警告视为错误时仍通过 |
| 非集成测试（含 API 与脱敏回归） | 61 passed，1 deselected |
| `python -m pip check` | `No broken requirements found.` |
| 实际 Uvicorn `/api/v1/health` | HTTP 200，健康响应保持不变 |
| 实际 Uvicorn `/api/v1/ready` | HTTP 200，数据库状态为 ready |
| 真实 PostgreSQL 集成测试 | 1 passed，61 deselected；真实 `SELECT 1` 返回 `1` |
| 全量 pytest | 62 passed，无失败、无跳过 |
| PostgreSQL Schema | 未执行 DDL，表定义只注册于 Python metadata |

首次集成检查因本地 PostgreSQL 未运行而连接超时。启动既有实例后已重新执行上述检查，所有命令退出状态均为 0；本次真实数据库验证通过。

Stage 02A 验证日期：2026-11-17（Windows PowerShell）。

| 检查项 | 实际结果 |
| --- | --- |
| Python 虚拟环境 | Python 3.11.6，复用 `backend/.venv` |
| PostgreSQL | PostgreSQL 17.12，`127.0.0.1:5432` 可连接 |
| SQLAlchemy `SELECT 1` | 真实数据库返回 `1` |
| `python -m pytest -q -m integration` | 1 passed，5 deselected |
| `python -m pytest -q` | 6 passed |
| `python -m pip check` | `No broken requirements found.` |
| API 健康检查 | `/api/v1/health` 返回 HTTP 200 |
| 数据库就绪检查 | `/api/v1/ready` 返回 HTTP 200 和 `{"status":"ok","database":"ready"}` |
| 凭据保护 | `backend/.env` 被 Git 忽略且未被跟踪；测试和响应未输出凭据 |

Stage 01 基线验证日期：2026-11-16（Windows PowerShell）。

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
