# PaperPilot ResearchOps

PaperPilot ResearchOps 是面向 AI 科研工作流与实验智能管理的全栈平台。Stage 02C 已完成 Alembic 迁移体系、独立测试库演练及本地 PostgreSQL 开发数据库的初始迁移。当前本地 `paperpilot_dev/public` 已应用 revision `20261120_0001`，包含三张业务表和 Alembic 版本表；此状态仅指已验证的本地开发环境，不代表云端生产环境已部署。

## 阶段状态

| 阶段 | 状态与范围 |
| --- | --- |
| Stage 01 | 完成：Next.js、FastAPI、基础健康检查和测试 |
| Stage 02A | 完成：PostgreSQL 连接、Engine/Session、Settings 与 readiness |
| Stage 02B | 完成：User、Workspace、WorkspaceMember ORM、约束及关系 |
| Stage 02C | 完成：Alembic 初始迁移、独立测试库验证、本地开发库迁移和备份 |
| Stage 03 | 待开发：注册、登录、密码哈希、认证、工作空间权限管理及租户隔离策略 |

当前没有用户注册、登录、认证、请求级授权或多租户安全隔离。ORM 关系和数据库约束不等同于访问控制。

## 技术栈

- Frontend: Next.js 16, React 19, TypeScript, Tailwind CSS 4, App Router
- Backend: Python 3.11, FastAPI, Uvicorn, SQLAlchemy 2.x, Psycopg 3, Alembic 1.20.1
- Database: PostgreSQL 17
- Testing: pytest, HTTPX / FastAPI TestClient

## 环境准备（Windows PowerShell）

需要安装：

- Python 3.11（可通过 `py -3.11` 调用）
- Node.js 24 与 npm 11
- Git
- PostgreSQL 17（程序和数据目录按各自本机安装配置）

首次准备后端：

```powershell
Set-Location backend
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

如果 PowerShell 阻止激活脚本，可以不激活环境，直接使用 `.\.venv\Scripts\python.exe` 执行后端命令。

### PostgreSQL 与后端配置

检查 PostgreSQL 状态；使用 ZIP 版本时，先将下面的占位符替换为自己的本机目录：

```powershell
$PgBin = "<PostgreSQL bin 目录>"
$PgData = "<PostgreSQL 数据目录>"
& (Join-Path $PgBin "pg_ctl.exe") status -D $PgData
# 仅在确认实例未运行且数据目录正确时启动；不要重复启动正在运行的实例。
& (Join-Path $PgBin "pg_ctl.exe") start -D $PgData -l (Join-Path $PgData "postgresql.log")
& (Join-Path $PgBin "pg_isready.exe") -h 127.0.0.1 -p 5432 -d paperpilot_dev
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

模型单元测试不依赖 `.env`，只在启用外键检查的 SQLite 内存库中验证约束，并离线编译 PostgreSQL DDL。SQLite 与 PostgreSQL 的大小写、长度和时区行为存在差异，详细边界见架构文档。仓库内的真实 PostgreSQL 集成测试仍只执行只读 `SELECT 1`；另行授权的原生迁移演练见本地验证记录。

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
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   ├── script.py.mako
│   │   └── versions/20261120_0001_create_initial_tables.py
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
│   │   ├── test_migrations.py
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

Stage 02B 建立统一 Base.metadata、ORM 字段、关系、约束和离线测试。Stage 02C 已完成初始迁移，并在独立测试库及本地开发库应用 `20261120_0001`。所有权只由 Workspace.owner_id 表示，成员角色仅限 admin/editor/viewer；认证和请求级权限管理属于 Stage 03。当前完成的是本地数据库基础设施，不包含 Docker、Redis、云部署、研究 Agent 编排或大模型 API。

## Alembic 迁移（Stage 02C 已完成）

Alembic 通过有序 revision 管理数据库 Schema 版本。配置位于 `backend/alembic.ini`，环境入口为 `backend/alembic/env.py`，模板为 `backend/alembic/script.py.mako`，历史脚本位于 `backend/alembic/versions/`，迁移测试为 `backend/tests/test_migrations.py`。

唯一初始 revision 为 `20261120_0001`（`create_initial_tables`），父版本为 base。它按依赖顺序创建 `users`、`workspaces`、`workspace_members`，包含既有 ORM 的字段、命名约束、外键删除策略和索引。UUID 不增加数据库默认值；`updated_at` 仍由 SQLAlchemy 更新，没有数据库更新触发器。历史迁移自包含，不导入会随业务变化的模型。

本地开发库的 `public` Schema 已有 `users`、`workspaces`、`workspace_members` 和 `alembic_version`。版本表唯一记录为 `20261120_0001`；业务表包含 3 个主键、3 个外键、4 个 CHECK 和 3 个二级索引，版本表另有自身主键。

在 `backend` 目录使用项目虚拟环境查看脚本历史和生成 SQL：

```powershell
.\.venv\Scripts\python.exe -m alembic heads
.\.venv\Scripts\python.exe -m alembic history --verbose
.\.venv\Scripts\python.exe -m alembic upgrade 20261120_0001 --sql
# 只读查询当前配置所指向数据库的版本；先确认连接目标。
.\.venv\Scripts\python.exe -m alembic current
```

SQL 预览命令中的 **`--sql` 必须保留**：它仅生成从 base 开始的 PostgreSQL SQL，不连接数据库、不需要 `.env` 或真实密码，也不会改变目标库的版本。已经迁移的数据库也可以生成此预览，但不得把初始 SQL 再次直接应用到已有表。`heads` 和 `history` 查看脚本历史，`current` 查询当前配置所指数据库的实际版本；脚本 head 本身不能证明任何数据库已升级。

在线环境支持通过 `Config.attributes["connection"]` 注入连接；未注入时才使用现有 `app.db.session.get_engine()` 和 Settings。Stage 02C 的实际迁移使用 Settings 构建目标专用 Engine，显式设置数据库名，在真实连接上核验身份后注入，并在运行时禁止默认 Engine 回退及新建连接。不在 INI 中保存连接 URL；应用启动和模型导入不会自动执行迁移。

**新环境必须独立检查和应用迁移，不能因本机已成功就假设其他数据库已经建表。** 配置本环境连接后，核验目标身份、权限、已有对象和版本状态，审查离线 SQL，准备备份与恢复方案，再经明确批准，通过受控连接升级到指定 revision。已有表不能通过盲目 stamp 来认领，也不得重复应用初始 SQL。后续 Schema 修改必须新增 Alembic revision，不得修改已应用的历史迁移。

实际升级由外层 PostgreSQL 事务包裹，局部限定 `search_path=public`，提交前核对版本、表结构、约束和索引，提交后通过新只读连接复核。提交前失败应回滚并检查实际状态；提交时连接中断必须先标记 `COMMIT UNKNOWN`，重新只读核验，禁止盲目重试、stamp 或 downgrade。初始 downgrade 会删除业务表及其数据，不能把它当作无损恢复手段。

备份保存在仓库之外，以唯一时间戳命名、限制访问权限并记录 SHA-256。归档列表与完整 SQL 渲染只证明可读取，不等于恢复成功。Stage 02C 曾将迁移前空库备份恢复到独立测试库；这不代表含真实业务数据的恢复能力，也不代表迁移后归档已经演练恢复。未来迁移仍需重新备份和评估恢复方案；凭据、备份内容与本机私有路径均不得提交。

仓库内迁移单元测试捕获 Alembic 操作、比较 SQLAlchemy 结构并离线编译 SQL；在线配置测试使用替身连接，既有 PostgreSQL integration 测试仍只执行 `SELECT 1`。Stage 02C-2B 另行在独立 PostgreSQL 测试库完成了真实迁移和 17 组约束行为验证，使用 SAVEPOINT 处理预期异常并回滚所有测试数据；这项演练不属于每次 pytest 自动执行的测试。

## 本地验证记录

### Stage 02C 最终状态与复验

日期：2026-11-20；范围仅限本地开发环境。

| 检查项 | 证据来源与结果 |
| --- | --- |
| 测试库恢复、真实迁移和约束测试 | 历史执行记录：迁移成功，17 组 PostgreSQL 约束测试通过，测试业务数据全部回滚 |
| 本地开发库初始迁移 | 历史执行记录：事务提交成功，提交前与新连接提交后的结构核验一致 |
| 迁移前、迁移后备份 | 历史执行记录：归档生成、列表检查和完整 SQL 渲染通过；迁移后归档没有实际恢复演练 |
| 开发库当前状态 | 本轮只读核对：PostgreSQL 17.12，public，唯一 revision 20261120_0001，三张业务表及版本表存在 |
| 约束与数据 | 本轮只读核对：3 个业务主键、3 个外键、4 个 CHECK、3 个二级索引；业务表各 0 行；ORM 无差异 |
| 本轮文档更新后的完整 pytest | 79 passed，0 failed，0 skipped；退出码 0；使用后端目录的项目 .venv Python 3.11.6 |
| 本轮 pip check | No broken requirements found.；退出码 0 |
| 上次迁移后回归 | 历史执行记录：79 passed，0 failed，0 skipped；pip check 通过 |

### 历史阶段验证记录

以下为各阶段当时的执行快照，不表示当前数据库仍处于迁移前状态。

Stage 02C-1 验证日期：2026-11-20（Windows PowerShell；后端目录，项目 `.venv` Python 3.11.6）。

| 检查项 | 实际结果 |
| --- | --- |
| Alembic | 1.20.1；其余原有固定依赖未升级 |
| 迁移测试 | 新增 17 项，随非集成与全量测试通过 |
| 非集成测试 | 78 passed，1 deselected |
| PostgreSQL 只读集成测试 | 1 passed，78 deselected |
| 全量 pytest | 79 passed，0 failed，0 skipped |
| pip check | No broken requirements found. |
| revision history / heads | 唯一初始 revision 和 head：20261120_0001 |
| PostgreSQL 离线 SQL | 生成成功；字段、命名约束、索引和依赖顺序已核对 |
| 真实数据库 | 当时仅验证连接和离线脚本；后续真实迁移与当前状态见上方 Stage 02C 最终记录 |

上述命令退出状态均为 0；历史 Stage 02B 记录保留如下。

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
| PostgreSQL Schema | 当时仅定义 Python metadata；后续已在 Stage 02C 完成初始迁移 |

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
