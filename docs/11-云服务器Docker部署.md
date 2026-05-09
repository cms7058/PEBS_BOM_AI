# PEBS BOM 云服务器 Docker 部署说明

更新日期：2026-05-09

## 1. 本轮新增的生产部署文件

- `docker-compose.prod.yml`：云服务器生产部署 Compose。
- `.env.production.example`：生产环境变量模板。
- `apps/api/Dockerfile.prod`：后端生产镜像。
- `apps/web/Dockerfile.prod`：前端生产镜像。
- `.dockerignore`、`apps/api/.dockerignore`、`apps/web/.dockerignore`：减少构建上下文，避免上传本地缓存、数据库和密钥文件。

现有 `docker-compose.yml`、`apps/api/Dockerfile`、`apps/web/Dockerfile` 继续作为开发环境使用。

## 2. 生产部署前必须修改

在云服务器上复制生产环境变量：

```bash
cp .env.production.example .env.production
```

必须修改以下值：

```bash
WEB_ORIGIN=https://你的前端域名
NEXT_PUBLIC_API_BASE=https://你的API域名
API_CORS_ORIGINS=https://你的前端域名

MINIMAX_PLAN_API_KEY=你的真实Key

POSTGRES_PASSWORD=强密码
DATABASE_URL=postgresql+asyncpg://pebs:强密码@postgres:5432/pebs_bom

MINIO_ROOT_USER=强用户名
MINIO_ROOT_PASSWORD=强密码
```

注意：`NEXT_PUBLIC_API_BASE` 会在前端镜像 build 阶段写入。修改后必须重建 web 镜像。

## 3. 启动命令

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
```

查看服务：

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml ps
```

查看日志：

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml logs -f api
docker compose --env-file .env.production -f docker-compose.prod.yml logs -f web
```

停止：

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml down
```

## 4. 暴露端口建议

生产 Compose 只暴露：

- `3000`：Next.js 前端
- `8000`：FastAPI 后端

PostgreSQL、Redis、MinIO 不再对公网暴露端口，只在 Docker 内部网络访问。

建议在云服务器前面加 Nginx / Caddy：

- `https://你的前端域名` → `web:3000`
- `https://你的API域名` → `api:8000`

服务器安全组建议只开放：

- `80`
- `443`
- SSH 管理端口

## 5. 数据库迁移

生产后端启动命令会自动执行：

```bash
alembic -c alembic.ini upgrade head
```

生产环境已设置：

```bash
DB_AUTO_CREATE=false
```

因此不会依赖应用启动时 `create_all` 自动建表。

## 6. 当前已验证

在本机已完成：

```bash
pnpm --filter @pebs-bom/web build
PYTHONPYCACHEPREFIX=/Users/mingyue/PEBS_BOM/.pycache_tmp python3 -m compileall apps/api/app apps/api/scripts apps/api/alembic
git diff --check
```

当前本机没有安装 Docker，因此尚未执行实际镜像构建：

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml build
```

这一步需要在装有 Docker 的云服务器或本地 Docker 环境执行。

## 7. 上线后的安全动作

1. 首次登录后立刻修改超级管理员默认密码。
2. 不要把 `.env.production` 提交到 git。
3. 不要把 PostgreSQL / MinIO 端口开放到公网。
4. 定期备份 PostgreSQL volume 和 MinIO volume。
5. 后续应将当前开发版 token 机制替换为正式 JWT / Session 认证。
