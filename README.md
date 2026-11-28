# PaperPilot ResearchOps

PaperPilot ResearchOps 是面向 AI 科研工作流与实验智能管理的全栈平台。Stage 02C 已完成 Alembic 迁移体系、独立测试库演练及本地 PostgreSQL 开发数据库的初始迁移。当前本地 `paperpilot_dev/public` 已应用 revision `20261120_0001`，包含三张业务表和 Alembic 版本表；此状态仅指已验证的本地开发环境，不代表云端生产环境已部署。

## 阶段状态

| 阶段 | 状态与范围 |
| --- | --- |
| Stage 01 | 完成：Next.js、FastAPI、基础健康检查和测试 |
| Stage 02A | 完成：PostgreSQL 连接、Engine/Session、Settings 与 readiness |
| Stage 02B | 完成：User、Workspace、WorkspaceMember ORM、约束及关系 |
| Stage 02C | 完成：Alembic 初始迁移、独立测试库验证、本地开发库迁移和备份 |
| Stage 03A | 已合并，提供用户注册、邮箱规范化、密码策略和 Argon2id 哈希 |
| Stage 03B | 已合并，提供登录、短期 JWT Access Token 和当前用户查询 |
| Stage 03C-1 | 已合并：可复用身份依赖和工作空间 RBAC |
| Stage 03C-2 | 已通过第二轮独立审查并合并：Workspace CRUD、分页隔离与写事务保护；PostgreSQL 并发验证尚未执行 |
| Stage 03C-3 | 成员查询、直接添加、改权和移除已完成离线开发与回归，等待独立代码审查及另行授权的 PostgreSQL 并发验收 |
| 后续阶段 | 邀请接受、所有权转移、邮箱归属验证、PDF 文献与资源隔离 |

注册和登录是两次独立请求。后端已接入正式 Workspace CRUD 和成员管理 API；前端成员页面与 PDF 资源接口尚未实现。

## 技术栈

- Frontend: Next.js 16, React 19, TypeScript, Tailwind CSS 4, App Router
- Backend: Python 3.11, FastAPI, Uvicorn, SQLAlchemy 2.x, Psycopg 3, Alembic 1.20.1
- Authentication: pwdlib[argon2] 0.4.2, email-validator 2.4.0, PyJWT 2.15.1
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

## 用户注册（Stage 03A）

`POST /api/v1/auth/register` 接收 `Content-Type: application/json`。以下仅为虚构契约示例；自动化注册测试使用内存 SQLite 和依赖覆盖，不向现有 PostgreSQL 开发库或迁移演练库发送测试注册请求。

```json
{
  "email": " Researcher@Example.COM ",
  "password": "A-long-example-passphrase-2026"
}
```

成功响应示例（HTTP 201；ID 和时间均为示例）：

```json
{
  "id": "00000000-0000-4000-8000-000000000001",
  "email": "researcher@example.com",
  "is_active": true,
  "created_at": "2026-11-20T00:00:00Z",
  "updated_at": "2026-11-20T00:00:00Z"
}
```

| 状态码 | 含义 / 错误 detail |
| --- | --- |
| 201 | 创建成功，仅返回 id、email、is_active、created_at、updated_at |
| 409 | Email is already registered |
| 422 | Invalid registration input |
| 503 | Database is unavailable |
| 500 | Registration failed |

邮箱先去除首尾空白，使用 Pydantic EmailStr / email-validator 验证，再统一为小写；输入和规范化结果均必须为 ASCII，规范化结果最长 320 字符，库的邮箱语法限制也同时生效。V1 不支持国际化邮箱或显示名形式，不查询 DNS，也不证明邮箱属于调用者。保留 `+tag` 和地址中的点，不做服务商专有身份合并。

密码为 15–128 个 Unicode 码点、最多 1024 个 UTF-8 字节；不 strip、不改大小写、不要求字符种类组合。验证发生在哈希之前。pwdlib 的 `PasswordHash.recommended()` 使用 Argon2id 和随机 salt；数据库只保存哈希。请求密码由 SecretStr 包装且不进入 repr；响应使用公开字段白名单。422 使用固定错误内容，不返回 Pydantic 的 input、ctx 或原始 msg，避免非法类型、嵌套输入和解析错误回显密码。

同步路由复用原有 Session 依赖。服务先添加并 flush 用户，取得服务器默认值并构造公开响应，再 commit；失败会尝试 rollback，依赖负责 close。最终去重由 `uq_users_email_lower` 唯一索引保证，不能用预查询代替。PostgreSQL 仅在 SQLSTATE 为 23505 且诊断名称为该索引时返回 409；其他约束或编程错误不会被当作重复邮箱。数据库连接及配置故障返回脱敏 503，其余内部错误返回通用 500。注册路由单独保护校验、依赖初始化和依赖清理路径，应用不记录密码、哈希或原始数据库错误；SQLAlchemy 参数已隐藏，外部日志和 APM 也应避免收集敏感请求体。

CORS 保留原 Origin 白名单，允许 GET 与 POST，跨域 Cookie 保持关闭。注册完成后，客户端可调用登录接口取得 Access Token。

### 注册测试与 PostgreSQL 隔离

新增安全及 API 测试通过 `dependency_overrides` 注入独立内存 SQLite Session，启用外键，并在结束后关闭 Session、释放 Engine。新测试阻止真实 PostgreSQL 连接；既有 PostgreSQL integration 测试仍只有只读 `SELECT 1`。数据库约束及模拟的 PostgreSQL 诊断异常验证了去重路径，但不代表真实双连接并发注册已通过。

未来 PostgreSQL 注册写入测试仅预留 `paperpilot_auth_test` 名称；本阶段不创建、迁移或写入该库，也没有默认启用的 PostgreSQL 注册写入测试。后续必须另行批准，显式 opt-in（例如 `PAPERPILOT_AUTH_PG_TESTS=1`），检查实际数据库、Schema 和 revision，并拒绝 `paperpilot_dev` 与 `paperpilot_migration_test`。应使用显式测试连接、外层事务和 SAVEPOINT，使服务内部 commit 仍可由外层 rollback 撤销；真正的双连接并发验证另行设计和授权。

## 用户登录与当前账户（Stage 03B）

在本机生成独立的 JWT 密钥。可用 Python 的 `secrets.token_hex(32)` 生成 32 个随机字节的 64 字符十六进制表示。将结果私下填入本机 `backend/.env` 的 `JWT_SECRET_KEY`，重启后端使其生效。应为不同环境使用不同密钥，并将实际值保存在 Git、共享终端输出和日志之外。认证接口在首次使用时验证密钥。缺失或无效的密钥会让认证失败，`/health`、`/ready` 和 Alembic 的配置仍独立加载。

### 登录

`POST /api/v1/auth/login` 接收 `Content-Type: application/json`。以下是虚构请求。

```json
{
  "email": "researcher@example.com",
  "password": "A-long-example-passphrase-2026"
}
```

成功返回 HTTP 200 和 `Cache-Control: no-store`。

```json
{
  "access_token": "<ACCESS_TOKEN>",
  "token_type": "bearer",
  "expires_in": 900
}
```

登录沿用注册的邮箱规范化规则，使用 `lower(email)` 查询。密码原样送入 Argon2id，要求为非空字符串，最多 1024 个 UTF-8 字节，以兼容已有哈希。缺失或禁用的账户使用预计算的 Argon2id 哈希执行一次校验，以降低明显的响应耗时差异。数据库查询与不同哈希的成本仍可能产生时间差异。

### 当前账户

调用 `GET /api/v1/auth/me` 时传入 `Authorization: Bearer <ACCESS_TOKEN>`。服务固定使用 HS256、签发方 `paperpilot-researchops` 和受众 `paperpilot-api`。JWT 有效期为 900 秒，要求 `sub`、`iat`、`nbf`、`exp`、`iss`、`aud`、`token_type`，其中 `sub` 为用户 UUID，`token_type` 为 `access`。`/me` 先验证 Token，再按 UUID 读取数据库，仅向仍处于启用状态的用户返回与注册相同的公开字段。

| 状态码 | 错误 detail |
| --- | --- |
| 登录 401 | `Invalid credentials`，用于邮箱不存在、密码错误和账户禁用 |
| 当前账户 401 | `Invalid access token`，用于凭据缺失、无效、过期、用户不存在或已禁用 |
| 登录 422 | `Invalid login input`，用于请求体解析或字段校验失败 |
| 503 | `Authentication is unavailable` 或 `Database is unavailable` |
| 500 | `Authentication failed`，用于未预期的认证、解析或 Session 清理错误 |

两类 401 均含 `WWW-Authenticate: Bearer`。登录与当前账户接口只读取 PostgreSQL 用户数据。自动化测试使用合成密钥和内存 SQLite，通过依赖覆盖阻止连接真实 PostgreSQL，并使用 PyJWT 及 Argon2id 验证实际签发与验密。

Refresh Token、主动注销、OAuth、密码重置、邮箱验证和前端登录页面留待后续阶段。对外开放前需配置 HTTPS、请求大小上限、登录与注册限流及 Argon2 并发上限，确保代理和 APM 不记录密码、JWT 或 `Authorization` 头，并建立密钥轮换与认证事件监控流程。

## 工作空间授权基础（Stage 03C-1）

身份认证确认 Bearer Token 对应的当前启用用户。授权根据这名用户在目标工作空间的当前角色，判断是否允许执行路由要求的权限。

`CurrentIdentity` 返回不可变的 `user_id`。`workspace_permission(...)` 在一个只读 Session 中检查账户状态和目标工作空间，生成不可变的 `WorkspaceAccessContext`，包含 `workspace_id`、`owner_id`、`user_id` 和 `effective_role`。所有权以 `Workspace.owner_id` 为准。其他用户的成员记录按工作空间与用户 UUID 共同查找，角色变化在后续请求中生效。

| 权限 | Owner | Admin | Editor | Viewer |
| --- | --- | --- | --- | --- |
| `workspace:read`、`resource:read`、`member:read` | ✓ | ✓ | ✓ | ✓ |
| `resource:create`、`resource:update` | ✓ | ✓ | ✓ | — |
| `workspace:update`、`resource:delete`、`member:manage_basic` | ✓ | ✓ | — | — |
| `workspace:delete`、`workspace:transfer`、`member:manage_admin` | ✓ | — | — | — |

未知角色和权限默认拒绝。Owner 可以管理 admin、editor、viewer，Admin 只能管理 editor、viewer。成员管理同时检查目标现有角色和拟设置角色，拒绝管理所有者或自行改权，成员角色不能设置为 `owner`。

认证缺失、无效或账户已禁用返回 401 并携带 `WWW-Authenticate: Bearer`。工作空间不存在、用户没有成员关系或角色无效统一返回 404。已有访问资格但缺少操作权限返回 403。数据库不可用返回脱敏 503，其他故障返回通用 500。

本阶段交付身份依赖、权限策略和工作空间访问判定。端到端测试使用独立测试路由、内存 SQLite 和合成账户。Workspace CRUD、成员管理、所有权转移及 PDF 资源隔离由后续阶段接入。写操作需要在实际业务事务中使用同一个 Session 重新授权，并按操作设计权限撤销与并发控制。

## 工作空间 API（Stage 03C-2）

五个接口均注册在正式应用中，使用 `Authorization: Bearer <ACCESS_TOKEN>`；账户必须仍然启用。当前前端尚无 Workspace 页面。所有权唯一来源是 `Workspace.owner_id`；普通成员角色仍只允许 admin/editor/viewer，不创建 owner 成员记录。

| 方法与路径 | 权限 | 成功响应 |
| --- | --- | --- |
| `POST /api/v1/workspaces` | 启用账户 | 201，创建者为 Owner |
| `GET /api/v1/workspaces` | 启用账户，只列出可访问空间 | 200，items/total/limit/offset |
| `GET /api/v1/workspaces/{workspace_id}` | Owner / Admin / Editor / Viewer | 200，单个空间 |
| `PATCH /api/v1/workspaces/{workspace_id}` | Owner / Admin | 200，更新后的空间 |
| `DELETE /api/v1/workspaces/{workspace_id}` | 仅 Owner | 204，无正文 |

创建和修改请求都只接受 `{"name":"Solar Forecasting Research"}`。name 必须是 1–150 个字符的字符串，拒绝纯 Unicode 空白、NUL 和非法 Unicode；保留合法输入的首尾空白，不自动规范化名称。禁止额外字段，包括 owner_id、user_id、role、effective_role、id 和 timestamps；空 PATCH 同样返回 422。

单个空间的公开字段固定为 id、name、owner_id、created_at、updated_at、effective_role。列表默认 `limit=20&offset=0`，limit 为 1–100，offset 范围为 `0–9223372036854775807`（`2**63-1`）。越界、负数和非整数 offset 在访问数据库前返回脱敏 422；上限值可正常查询并在当前测试数据规模下返回空列表。SQL 按当前用户的所有权或合法成员关系过滤，并按 created_at、id 升序分页；拥有空间同时存在成员记录也不会重复。total 只统计该用户可访问的空间。并发改动可能使两次计数/分页查询观察到不同状态；不承诺分页期间的快照不变。

错误约定：身份或账户无效 401（Bearer challenge）；有效成员缺少操作权限 403；不存在或不可访问 404（`Workspace not found`）；非法输入 422；已识别的数据库不可用 503；其他内部错误 500。受保护响应含 `Cache-Control: no-store`，405 同时保留框架的 `Allow`。

### 写事务、锁协议与提交结果

POST/PATCH/DELETE 使用专用 `workspace_write_session`，不复用注册提交标记。服务在同一事务中检查启用账户并执行写入，先构造白名单响应；事务管理器在调用 commit 前再显式 flush。操作或显式 flush 失败时尚未调用 commit，按原错误类型返回脱敏失败并尝试 rollback。只有 commit 正常返回才确认提交成功并返回 201/200/204。close 失败时依次尝试 Session invalidate、保留 Connection 的 invalidate/close。清理日志仅使用固定消息，不记录异常、SQL 参数或凭据。已确认提交后的清理失败不抹掉真实成功；所有底层清理能力同时失效时仍需运营告警和连接处置，不能承诺故障驱动的物理释放一定成功。

PostgreSQL 写连接显式使用 READ COMMITTED、事务级 lock_timeout=5s 和 statement_timeout=15s。锁顺序为：当前 User `FOR SHARE` → 目标 Workspace `FOR UPDATE` → 当前 WorkspaceMember（存在时）`FOR UPDATE`；取得锁后再调用既有 RBAC 服务。创建只锁当前 User，再插入空间。账户禁用、所有权变更、已有成员降级/删除所需的更新锁与这些锁冲突，相关操作应串行完成；这些是设计预期，尚未执行真实 PostgreSQL 并发验证。

未来成员/所有权写服务必须遵循同一协议：先按 UUID 排序锁定所有需要的 User，再按 UUID 排序锁定 Workspace，最后按用户 UUID 排序锁定成员，不得先锁成员再反向等待 Workspace。账户禁用若涉及其他表也必须遵守顺序。死锁/序列化拒绝会安全失败，不自动重试。仅把代码放在同一 Session 不足以保证并发安全，详细协议见架构文档。

提交前由数据库拒绝的写入（例如显式 flush 或 DELETE 执行时的约束拒绝）与提交结果未知分开处理。一旦进入 `Session.commit()`，本实现无法可靠判定异常来自物理提交、提交过程中的隐式 flush，还是物理提交后的 `after_commit` 回调，因此所有普通 commit 异常统一保守返回 503：`Workspace commit outcome is unknown; reconcile before retrying`。即使异常是 IntegrityError 或携带 SQLSTATE 40001/40P01，也不能仅凭类型认定主事务已拒绝。after_commit 抛错时数据可能已经持久化；commit 内部隐式 flush 拒绝时数据也可能未提交，UNKNOWN 表示应用无法证明结果。rollback 不能撤销已提交事实，清理失败也不会覆盖 UNKNOWN。

客户端不得盲目重试 POST/PATCH/DELETE，应先核对实际 Workspace 状态。创建名称不是唯一标识，当前未实现幂等键，创建结果不确定时不能仅凭同名记录认定结果；PATCH 和 DELETE 同样需核对当前名称或资源是否仍存在。

### 数据库与测试边界

本阶段未修改 ORM 或 Alembic revision，未读取真实 `.env`，未连接或写入 PostgreSQL。自动化测试使用合成用户和启用外键的 SQLite 内存库，并通过故障注入检查实际数据。清理故障测试使用带独立 keeper 连接的命名内存数据库，避免池失效销毁数据而掩盖提交结果；不会创建磁盘数据库。

测试覆盖外键级联删除成员、保留 User 与其他空间、租户隔离、输入白名单、分页、提交拒绝/结果不确定和多层清理失败；PostgreSQL 锁语句及事务选项仅离线检查。真实行锁、撤权竞态、死锁、双连接约束与网络中断需另行授权，在独立测试库验证。

2026-11-26 第一轮审查定向修复新增 45 项正式回归。修改实现前的 42 项定向用例为 25 failed、17 passed，修改后同组全部通过；随后补充 3 项提交前真实数据库拒绝叠加清理故障用例。最终提交结果定向 36 passed、offset 边界 9 passed、事务文件 89 passed、CRUD 文件 78 passed、隔离文件 55 passed、原有认证/RBAC 311 passed；全部非集成测试 611 passed、1 deselected，pip check 与 git diff --check 通过。测试阻止真实 .env 读取与 Psycopg 连接。after_commit 故障使用真实 SQLAlchemy Session、正式 ASGI 接口和独立 Session 核查实际创建/更新/删除结果，属于 SQLite 人工注入，不是 PostgreSQL 网络故障或锁竞争实测。该修复后已通过第二轮独立审查并合并；此处保留开发验证记录，PostgreSQL 并发验收仍未完成。

Stage 03C-3 接入下述成员添加、改权与移除接口；所有权转移、PDF、文献、RAG、Agent 尚未实现。未来资源表加入后，必须先确定数据库关联删除、对象存储清理和失败恢复的一致性策略，不能直接假设删除 Workspace 会自动清理外部文件。公网部署安全待办仍包括限流、请求体总量限制、Argon2 并发控制、日志/APM 脱敏、密钥轮换和 HTTPS；历史非法 Argon2 工作参数分类待办未在本阶段修改。

## Workspace 成员 API（Stage 03C-3）

以下接口均使用 JWT，检查当前账户启用状态，并在正式应用注册：

| 方法与路径 | 成功响应 | 权限 |
| --- | --- | --- |
| GET `/api/v1/workspaces/{workspace_id}/members` | 200，items/total/limit/offset | member:read，四种角色均可 |
| POST `/api/v1/workspaces/{workspace_id}/members` | 201，MemberResponse | Owner 可添加三种角色；Admin 只可添加 editor/viewer |
| PATCH `/api/v1/workspaces/{workspace_id}/members/{user_id}` | 200，MemberResponse | Owner 可改普通成员；Admin 只可在 editor/viewer 之间改权 |
| DELETE `/api/v1/workspaces/{workspace_id}/members/{user_id}` | 204，无正文 | Owner 可移除普通成员；Admin 只可移除 editor/viewer |

POST 请求为 `{"user_id":"<UUID>","role":"viewer"}`，两个字段均必填；PATCH 只接受必填的 `{"role":"editor"}`。角色严格限于小写 admin/editor/viewer。非法 UUID、非法角色、空 PATCH 和额外字段返回脱敏 422；操作者只能来自已验签 JWT 的 sub，不能提交 owner_id、actor_id、current_role、时间戳或通过 PATCH 的 user_id 改写身份。

公开成员字段仅有 user_id、role、joined_at，不公开邮箱、密码哈希或账户内部信息。列表中 Owner 是由 Workspace.owner_id 生成的虚拟记录，role=owner、joined_at=null，始终排在首位；其余成员按 joined_at、user_id 升序排列。已有冗余 Owner 成员行会被去重，非法成员角色被排除；接口不会创建 Owner 成员行。默认 limit=20、上限 100，offset 范围 0–9223372036854775807。total 包含唯一虚拟 Owner；计数和分页 SQL 都限定目标 Workspace 及当前调用者访问资格，不承诺并发时的冻结快照。

Editor/Viewer 只能读取成员，非成员与不存在的 Workspace 统一返回 404 `Workspace not found`。具有访问资格但管理权限不足返回 403。任何人都不能经成员 API 管理真实 Owner 或自己的成员记录；Admin 不能管理其他 Admin，也不能把任何成员晋升为 Admin。相同角色 PATCH 在完整授权后返回 200 且不执行 UPDATE；重复 DELETE 返回成员 404。重复 POST 仅在调用者有权管理当前角色及请求角色时返回 409 `Member already exists`，否则仍拒绝为 403。

添加只支持已经注册且启用的目标账户。先做只读预授权，不合格请求不会查询目标账户状态或取得目标 User 锁；最终权限仍必须在完整锁定后重新读取。已授权管理者的未知/禁用目标账户与不存在成员均使用固定 404 `Member not found`。已有禁用成员仍可由合格管理者改权或移除，禁用不会自动清除成员关系。删除成员只删除目标关联，不删除 User、Workspace 或其他空间的成员。

成员管理是受权限限制的直接添加模式，不等于邀请接受；本阶段不提供用户搜索、邮件邀请、成员自行退出或所有权转移。移除或降级提交后，后续请求从数据库重新判定权限，不更改该用户其他 Workspace 的角色。

### 成员写入锁、事务和验证边界

共用的锁辅助函数位于 `services/workspace_access.py`，Workspace CRUD 与成员写入都调用它。成员写入的行锁顺序是：按 UUID 排序的操作者和目标 User `FOR SHARE` → Workspace `FOR UPDATE` → 按用户 UUID 排序的操作者和目标成员 `FOR UPDATE` → 重新读取操作者权限和目标当前角色 → can_manage_member 校验 → 写入。预授权结果不替代锁后授权，不能先锁 Workspace 再回头锁目标 User。

不存在的成员行不能被 SELECT FOR UPDATE 锁定，因此合作写事务以 Workspace 行锁串行化，再由复合主键兜底。预检查发现重复返回 409；显式 INSERT flush 发生已知主键拒绝也可返回 409，其他约束错误不会一律当作重复。这是提交前业务分类，不改变共用 `workspace_write_session()` 的规则：commit 开始后抛出的普通异常仍为 UNKNOWN 503，文案保持 `Workspace commit outcome is unknown; reconcile before retrying`；确认 commit 成功后的清理故障保留成功，不自动重试。UNKNOWN 客户端应先查询成员现状。

离线设计预期：两个管理者添加相同成员由 Workspace 锁排序，后执行者重新检查重复；Owner 改权与 Admin 改权、Admin 被降级/移除与其写入也按 Workspace/成员锁排序并重新授权；Workspace 删除先完成则成员写入 404，成员写入先完成则随后删除可级联该关系；目标禁用先完成则拒绝新增，添加先取得 User SHARE 锁则禁用更新需等待。相反操作者/目标顺序必须采用相同的 User UUID 排序。以上不是实际 PostgreSQL 并发证明。

2026-11-26 在既有 Python 3.11.6 环境中新增 256 项测试：API/权限 144 passed、隔离 45 passed、事务 67 passed。全量非集成测试 867 passed、1 deselected、0 failed、0 skipped，pip check 和 git diff --check 通过，所有最终命令退出码 0。历史 611 项保留，使用合成账户/JWT、启用外键的 SQLite 内存库和正式 ASGI 请求；独立 Session 核查插入、更新、删除及提交后异常的真实结果。过期重复预检查测试验证了真实主键拒绝，不代表实际双连接竞争。

测试进程禁止真实 .env 读取和 Psycopg 连接；未修改 ORM、迁移或依赖。真实 PostgreSQL 的双连接角色降级、成员移除、重复添加、Workspace 删除竞争、账户禁用竞争、锁等待、死锁回滚、READ COMMITTED 可见性、提交应答丢失和网络中断，必须另行授权独立测试库验收。不得用 paperpilot_dev 或 paperpilot_migration_test 写测试代替。

保留的 P3：FastAPI 生成的响应 OpenAPI 组件可能将 int64 maximum 转为浮点表示；请求参数及实际 Pydantic 边界是精确整数，本轮未修复该文档表示问题。已有部署安全待办继续保留。后续 PDF、文献及 RAG 资源必须绑定 Workspace，并在每次查询和写入中执行对应授权，成员接口本身不提供资源隔离实现。


## PostgreSQL 测试安全入口（Stage 03C-4B-0）

本阶段只实现和离线验证测试基础设施。第一轮独立审查结论为 **FAIL**：原有 1017 项测试通过，没有覆盖可自构造的授权对象、缓存连接、原生别名、早期插件及租约撤销顺序。下表记录本轮定向修复；仍须第二轮独立审查和人工验收。

| 发现 | 修复 |
| --- | --- |
| F1：自构造 PgOptIn 获得连接许可 | PgOptIn 仅表达离线意图；真实许可绑定隔离进程、实际 pytest Config、当前标记测试、运行阶段和双重开关 |
| F2：缓存 Engine/Session/Connection 及旧入口绕过 | 独立新进程；启动时拒绝未登记 PG Engine；Engine、SQL、提交执行门；旧集成入口检查可信 live Engine |
| F3：函数别名及 binary PQ 导出 | 覆盖当前安装版本的 Python/原生模块导出和真实生成器链；已保存的 C 引用仍是明确的进程内保护边界 |
| F4：早期插件先于守卫 | 可信启动器在导入 pytest 前安装守卫；禁用插件自动加载和环境注入，只接受静态批准的插件 |
| F5：失效 .info 隐式重连 | 先检查 closed/invalidated，再访问 .info/DBAPI；初始化回调也遵守此顺序 |
| F6：准入及清理失败留下旧租约 | 先从独立弱引用注册表撤销租约，再检查及清理；清理全失败也不恢复许可 |
| F7：熔断后借出连接仍执行 | 每次执行及 commit 检查 Engine/当前运行期/租约；熔断、dispose 同时撤销所有租约 |
| F8：启动测试预装被测守卫 | 子进程只预装独立物理替身；正式启动链自行安装项目守卫，验证早期钩子、收集、不同目录和错误入口 |

### 启动入口与信任边界

推荐默认离线入口，从 backend 运行：

```powershell
.\.venv\Scripts\python.exe -B run_pg_tests.py -q
```

启动器校验路径与选项，清除应用 DB/JWT、PYTEST 插件/参数注入和 Python 路径注入环境，另起 `python -I -B` 进程。bootstrap 先安装环境审计、驱动、SQLAlchemy 和数据库库日志脱敏，再导入 pytest。当前仅批准安全插件及 `tests.postgres._startup_probe` 审计插件；拒绝任意 `-p`、`-c`、`-o` 和仓库测试目录以外的路径。

普通 `python -m pytest` 仍默认跳过旧 `integration` 和三个 `postgres_*` 组；root conftest 在应用 fixtures 前阻止真实 .env 和支持的 PG 入口。它无法抢在任意手工注入的早期第三方插件之前执行，所以不能用于申请真实 PG 许可。给普通 pytest 传 `--pg-test` 或 `--pg-fault` 会拒绝；真实测试必须走可信入口。上述保护只作用于测试进程，不修改正式应用。

未来另行获得数据库操作授权并完成环境隔离后，基础测试必须同时使用可信入口的 `--pg-test` 和精确 `PAPERPILOT_PG_TEST_ENABLE=1`；故障测试还需 `--pg-fault` 和 `PAPERPILOT_PG_TEST_FAULT_ENABLE=1`。收集、早期钩子和测试结束后没有连接许可。只有当前已标记测试的 setup/call/teardown 阶段可用；跨测试缓存 live Engine 会因运行期 nonce 改变而失效。技术开关不代表本轮获准连接数据库。

### 专用配置与二次身份核验

未来为独立 PostgreSQL 17 测试实例做环境验收时，须核验生效的 `max_prepared_transactions = 0`，由服务器禁用 prepared transactions。此项为待验收要求，当前尚未核验。客户端测试守卫仍须独立拒绝两阶段事务控制命令。

Settings 禁用 dotenv 和文件 secrets，无应用默认值；以下字段全部必填：

| 专用环境变量 | 约束 |
| --- | --- |
| PAPERPILOT_PG_TEST_ENABLE | 精确 1 |
| PAPERPILOT_PG_TEST_HOST | 精确 127.0.0.1 |
| PAPERPILOT_PG_TEST_PORT | 精确 55432 |
| PAPERPILOT_PG_TEST_DB_NAME | 精确 paperpilot_concurrency_test |
| PAPERPILOT_PG_TEST_USER | 精确 paperpilot_test_app |
| PAPERPILOT_PG_TEST_PASSWORD | 独立非空测试密码；SecretStr，不写入 Git/聊天 |
| PAPERPILOT_PG_TEST_ENVIRONMENT_ID | 规范小写 UUID4，与服务器标识相同 |

拒绝继承的全部 `PG*` libpq 环境变量、开发端口、多 Host、socket、开发库及历史迁移库；使用 URL.create，不拼接密码。独立合成 AuthSettings 使用 `_env_file=None`，不复用开发 JWT 密钥。错误采用固定信息，测试进程在 LogRecord 进入任何日志 handler 前清除 psycopg/sqlalchemy 的消息参数和异常详情，包括 DEBUG 和连接池清理故障。应用 logger 不受此规则改写；测试期间数据库库详细日志有意不保留。

专用 Engine 使用 READ COMMITTED、独立四连接池和有界超时。首次 PostgreSQL 方言初始化仅临时允许绑定当前 Engine/Connection/DBAPI 的三条固定只读查询（version/current_schema/standard_conforming_strings），结束即撤销，不能用于业务 SQL。禁用非必要 hstore 查询和 pool_pre_ping；每次租用完整核验替代 ping。

正式核验在有界只读事务内检查实际库名、用户、地址/端口、17.x、search_path、最小权限、数据库/表 Owner、环境标识、唯一 revision 和反射 Schema，包括全局 lower(email) 唯一索引及完整 FK/CHECK。核验事务 rollback 成功才发放连接/驱动绑定的逻辑租约。驱动或初始化失败同样熔断；回收、invalidate、detach、dispose 和失败均撤销许可。允许清理 rollback/close，但清理失败不能恢复业务执行资格。

环境标识契约为 `test_control.environment` 的 id=1、UUID4 和 purpose=`paperpilot-postgres-tests`，由 `paperpilot_test_owner` 持有；普通测试角色不得修改标识、Schema 或版本表。数据库、业务表也要求该 Owner。当前没有建立专用实例、数据库、角色或标识；候选实例目录 `D:\PostgreSQL\paperpilot_concurrency_17` 及端口均未实测。

测试迁移仅准备绑定已核验连接、租约和 revision `20261120_0001` 的 Alembic Config；缺失/替换/过期连接会拒绝，不回退应用 Engine。模块不执行 upgrade/downgrade/stamp。空库首次初始化需要单独 Owner/Migrator 设计；不能用 create_all 冒充迁移。

### 尚未实现的环境边界

Python 私有变量、dataclass、ContextVar 和 Monkeypatch 都不是恶意 Python 沙箱。测试证明：保存的 Python connect 别名仍经过受保护的真实生成器链；**预先捕获的原生 C 函数引用无法靠替换模块导出撤销**。边界测试特意触达外层原生替身一次，不把它改报成项目守卫拦截。

可信入口只加载经过审查的仓库测试、固定插件和受信任虚拟环境依赖。默认离线入口不需要数据库凭据，且清除应用连接环境。未来真实测试还必须具备专用低权限角色、独立实例、凭据隔离及操作系统/网络目标限制；这些尚未创建或验证。后台 worker 必须在测试许可结束前停止并归还连接；当前设施不能代替未来真实并发、提交结果不确定和网络隔离验收。

### 定向修复离线记录（2026-11-27）

| 检查 | 实际结果 | 退出码 |
| --- | --- | --- |
| F1–F8 与补充定向回归 | 60 passed | 0 |
| 安全测试目录 | 210 passed | 0 |
| 全部非集成测试 | 1077 passed，1 deselected | 0 |
| 默认 pytest | 1077 passed，1 skipped | 0 |
| pip check | No broken requirements found. | 0 |
| git diff --check | 通过 | 0 |

原有 150 项安全测试和 867 项业务测试保留，新增 60 项。首批正式复现修复前 12 failed，修复后同组 12 passed；后续还先复现并修复真实方言初始化、库内部日志和初始化失效连接路径，最终 60 项全部通过。此记录没有把旧 55 项临时探针宣称为逐字重跑后的全部通过。

四组最终 pytest 均仅预装独立物理替身，未预装被测项目守卫。每组子进程汇总替身触达 4 次：3 次受控运行期正向/日志探针，1 次已保存原生引用边界探针；主进程原生替身触达 0，真实 PostgreSQL 连接 0。其余拒绝场景断言在项目守卫处阻止。安全目录及全量回归中的一次环境审计计数来自原测试直接发出 sys.audit 事件，没有打开或读取 .env；所有子进程环境审计计数为 0。默认跳过的旧 SELECT 1 不计作原生成功。

真实 SELECT 1、在线迁移、PG 原生行锁/双连接并发、网络故障及提交应答丢失均未执行。初始化目录、实例启停、真实连接、建库建角色、GRANT/REVOKE、环境标识 DDL、写入/清理和故障注入仍须另行明确授权。

### 第二轮审查问题定向修复（G1–G3）

当前基线为 1089 项非集成回归，包括额外的 12 项注册清理测试；上表 1077 项是历史记录。
第二轮独立审查发现 raw DBAPI、两阶段提交和 SAVEPOINT 清理缺口。本轮只修改测试安全设施与回归，不改变业务 API、认证、Schema 或前端。

- G1：`Engine.connect()` 内部建立短期借出资格；直接 `Engine.raw_connection()` / `Pool.connect()` 拒绝。每次 checkout 创建新的受控 DBAPI 句柄，SQL、批量 SQL、结果读取及 commit 复查当前连接租约、运行期与熔断；归还池后的旧句柄不会随物理连接复用而恢复权限。rollback/close 保留有界清理用途。
- G2：测试 Engine 不支持两阶段事务，SQLAlchemy 事件、方言方法和 DBAPI `tpc_*` 都禁止 begin/prepare/commit。对已登记的历史事务仅允许精确绑定的回滚；不自动接管外部 XID 或执行 recover。
- G3：熔断后的嵌套回滚仅接受当前真实 `NestedTransaction`、当前根事务和此前核验过的 checkout。一次性清理资格绑定确切 `RollbackToSavepointClause` 及其编译 SQL；直接文本、其他名称、跨连接、批量执行和过期上下文都不能借用。回滚异常会撤销资格、作废连接并返回脱敏错误。

受控方言初始化保持原有三条 `exec_driver_sql` 查询及固定隔离级别读取。核验阶段的 DBAPI 只接收实际 SQLAlchemy 执行上下文中批准的只读核验查询；未完成核验的原始 SQL/commit 不因核验上下文而获准。代理不提供任意 Psycopg 扩展方法或原生 `pgconn` 接口。

专项回归可以独立执行：

```powershell
.\.venv\Scripts\python.exe -B run_pg_tests.py -q tests/postgres/test_raw_connection_guard.py tests/postgres/test_twophase_guard.py tests/postgres/test_savepoint_cleanup.py
```

正式测试不依赖审查临时目录。验证使用独立外层原生驱动替身，并区分模拟调用与真实连接。当前工作仍只适用于离线验证，必须继续独立复审；不授予 Commit、Push、PR 或真实 PostgreSQL 操作权限。

本轮最终离线结果（2026-11-28）：

| 检查 | 结果 | 退出码 |
| --- | --- | --- |
| G1–G3 专项回归 | 52 passed | 0 |
| 安全测试目录 | 262 passed | 0 |
| 全部非集成测试 | 1141 passed，1 deselected | 0 |
| 默认 pytest | 1141 passed，1 skipped | 0 |
| pip check / git diff --check | 通过 | 0 |

首批 37 项修复前为 23 failed、14 passed；随后补齐边界用例，最终新增 52 项。原有 1089 项保留；仅规范化既有 `protected_405` 参数在收集时生成的随机 UUID 后，旧 1090 个节点（含 integration）缺失 0。默认 pytest 保持当前已安装插件自动加载；测试仅禁用 cache 写入，应用凭据及注入变量由外层隔离。

最终专项的外层原生替身与内置物理替身均触达 0 次；安全目录、非集成及默认 pytest 各有 4 次明示的内置替身正向/边界控制，独立外层原生替身均为 0。后面三组各一次环境审计来自显式 `sys.audit` 模拟，没有打开 `.env`。真实 PostgreSQL 连接、真实数据库 SQL和在线迁移均为 0。

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

`integration` 已纳入统一 PostgreSQL 安全入口，默认跳过；不会读取真实 `backend/.env`。普通回归与测试收集不连接 PostgreSQL。未来需双重 opt-in、专用白名单配置和服务器身份核验，且必须另行获得数据库操作授权。安全跳过和 Mock 不能算原生验证通过。

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
│   │   ├── api/dependencies.py
│   │   ├── api/protected_route.py
│   │   ├── api/request_errors.py
│   │   ├── api/v1/auth.py      # 注册路由及脱敏边界
│   │   ├── api/v1/login.py     # 登录和当前账户路由
│   │   ├── api/v1/workspaces.py # 五个正式 Workspace 路由
│   │   ├── core/authorization.py
│   │   ├── core/config.py
│   │   ├── core/security.py    # 密码策略和 Argon2id
│   │   ├── core/token.py       # 签发与校验 Access Token
│   │   ├── db/base.py
│   │   ├── db/session.py
│   │   ├── db/workspace_session.py # Workspace 专用写事务
│   │   ├── models/             # User、Workspace、WorkspaceMember
│   │   ├── schemas/auth.py     # 认证请求与公开响应
│   │   ├── schemas/workspace.py
│   │   ├── services/authentication.py
│   │   ├── services/registration.py
│   │   ├── services/workspace_access.py
│   │   ├── services/workspaces.py
│   │   └── main.py
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── conftest.py
│   │   ├── test_auth_dependencies.py
│   │   ├── test_config.py
│   │   ├── test_current_user.py
│   │   ├── test_database_integration.py
│   │   ├── test_database_reporting.py
│   │   ├── test_health.py
│   │   ├── test_login.py
│   │   ├── test_models.py
│   │   ├── test_rbac.py
│   │   ├── test_migrations.py
│   │   ├── test_registration.py
│   │   ├── test_security.py
│   │   ├── test_readiness.py
│   │   ├── test_token.py
│   │   ├── test_workspace_access.py
│   │   ├── test_workspace_crud.py
│   │   ├── test_workspace_isolation.py
│   │   └── test_workspace_transactions.py
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

Stage 02B 建立统一 Base.metadata、ORM 字段、关系、约束和离线测试。Stage 02C 已完成初始迁移，并在独立测试库及本地开发库应用 `20261120_0001`。Stage 03A 注册、Stage 03B 登录、Stage 03C-1 权限判定、Stage 03C-2 Workspace CRUD 和 Stage 03C-3 成员 API 均沿用该 Schema。前端认证/成员页面、邀请接受、成员自行退出、所有权转移、OAuth、Docker、Redis、云部署、研究 Agent 编排和大模型 API 均在当前范围之外。

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

### Stage 03C-2 初版离线验证（历史记录）

2026-11-25，使用现有 `.venv` Python 3.11.6，通过带真实 `.env` 读取和 PostgreSQL 连接拦截的测试进程执行。禁用字节码及 pytest 缓存，合成测试文件仅位于临时目录；所有新增数据均在 SQLite 内存中。

| 检查项 | 实际结果 | 退出码 |
| --- | --- | --- |
| `tests/test_workspace_crud.py` | 69 passed | 0 |
| `tests/test_workspace_isolation.py` | 55 passed | 0 |
| `tests/test_workspace_transactions.py` | 53 passed | 0 |
| Stage 03A/03B/03C-1 专项回归（8 个原有文件） | 311 passed | 0 |
| 全部非集成测试 | 566 passed，1 deselected，0 failed，0 skipped | 0 |
| pip check | No broken requirements found. | 0 |
| git diff --check | 通过 | 0 |

新增 177 项测试，原有 389 项全部保留并通过。唯一 deselected 是既有真实 PostgreSQL 集成测试。未执行 PostgreSQL 并发、写入或在线迁移；行锁及事务参数只有离线检查证据。以上是开发验证，不代替 Stage 03C-2 的独立代码审查。

### Stage 03A 验证

2026-11-20，使用后端项目 `.venv` Python 3.11.6 实际执行：

| 检查项 | 本轮实际结果 | 退出码 |
| --- | --- | --- |
| 安全模块 `tests/test_security.py` | 20 passed | 0 |
| 注册 API `tests/test_registration.py` | 54 passed | 0 |
| 全部非集成测试 | 152 passed，1 deselected | 0 |
| 全量 pytest（含既有 PostgreSQL SELECT 1） | 153 passed，0 failed，0 skipped | 0 |
| pip check | No broken requirements found. | 0 |

新增 74 项测试，原有 79 项全部通过。测试进程限制 PostgreSQL 连接为只读，注册 API 测试额外阻止真实 PostgreSQL 连接；前后只读核对开发库与迁移演练库的版本、结构及空表状态保持一致。Stage 02C 的以下历史记录保持独立，不能替代本阶段测试。真实 PostgreSQL 注册写入与双连接并发验证未执行，需另行授权独立测试库。

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
