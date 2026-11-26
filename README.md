# 回收托盘板材含水混料分拣

托盘再利用工场导入来货板件的测湿台账。后台按整批切分历史、留出和待分拣记录，用高斯混合模型估计相对干湿成分，并按后验概率、成分距离和库位覆盖情况生成分拣清单。

| 模块 | 业务产出 |
|---|---|
| 来货板件测湿台账 | CSV 导入、板件唯一性与整批分区校验、台账 SHA-256 |
| 高斯混合拟合与 BIC 选型 | EM 候选模型、BIC、冻结成分和训练来源 |
| 库位对账与待分拣清单 | 独立批评分、库位密度诊断、带原因的人工复测单 |
| 复测交班签核 | 签核结果、审计记录、获批 CSV |

## 启动与账号

在项目目录执行以下命令。首次启动准备 `.env` 时可从 `.env.example` 复制，并核对 `BACKEND_PORT=19436`、`DB_PORT=17936`。`SEED_DEMO=true` 时启动加载演示台账，手工运行 seed 可核对行数和校验和。

```bash
docker compose build backend
docker compose up -d --no-build --wait
docker compose exec -T backend python -m scripts.seed_demo
```

| Compose 服务 | 用途 | 地址 |
|---|---|---|
| `backend` | Python 3.11 / FastAPI 登录、台账、签核和导出 | `http://127.0.0.1:19436/api` |
| `worker` | 后台拟合、密度评分和队列生成 | Compose 内网 |
| `db` | PostgreSQL 16 台账、运行、签核和审计 | `127.0.0.1:17936` |
| `redis` | Redis 7 作业通知和 worker 心跳 | Compose 内网 |

Compose 项目名为 `yangyang-pallet-mixture`。OpenAPI 位于 `http://127.0.0.1:19436/api/openapi.json`。环境配置包括服务端口、`COMPOSE_PROJECT_NAME`、`JWT_SECRET`、`SEED_DEMO`，应用通过 `DATABASE_URL` 和 `REDIS_URL` 访问存储。

| 角色 | 邮箱 | 演示密码 | 权限 |
|---|---|---|---|
| 分析员 `analyst` | `analyst@demo.local` | `Demo-2611!` | 导入台账、提交计算、查看计算和下载已获批 CSV |
| 复核员 `admin` | `admin@demo.local` | `Demo-2611!` | 分析员权限、签核和审计查询 |
| 查阅员 `viewer` | `viewer@demo.local` | `Demo-2611!` | 查看台账和已获批运行、下载已获批 CSV |

## API 工作流

通过 `POST /api/auth/login` 提交 `email` 和 `password`。业务请求使用 `Authorization: Bearer <access_token>`。

| 操作 | 方法与路径 | 输入或状态 |
|---|---|---|
| 查看列与配置 | `GET /api/spec` | CSV 字段顺序、运行参数和模块名称 |
| 导入测湿台账 | `POST /api/datasets` | JSON 对象包含 `name` 和完整 CSV 文本 `csv`，成功返回 201 |
| 查找演示台账 | `GET /api/datasets` | 台账名为 `20261125-回收托盘板材测湿.csv` |
| 提交后台任务 | `POST /api/runs` | 提交 `label`、`dataset_id` 和可选 `settings`，成功返回 202 和运行编号 |
| 查看结果 | `GET /api/runs/{id}` | `completed` 时读取 `result.tables`，`failed` 时读取 `error` |
| 签核 | `POST /api/runs/{id}/review` | `admin` 可提交 `{"decision":"approved","note":"已核对复测件与库位偏移"}` |
| 下载获批 CSV | `GET /api/runs/{id}/export/{key}` | `approved` 后可按表键下载 |
| 查看审计 | `GET /api/audit` | `admin` 查询最近操作 |

| 表键 | 内容 |
|---|---|
| `sorting_queue` | 待分拣板件、首选与次选成分、操作与复测原因 |
| `bic_candidates` | 候选成分数、起点收敛情况、训练对数似然和 BIC |
| `model_components` | 成分权重、均值、方差、训练批次及复测阈值 |
| `holdout_scoring` | 独立批逐板评分，供复核对账 |
| `location_diagnostics` | 按分区和库位聚合的批数、平均对数密度和复测比例 |

下面的命令用已获批运行编号 `run_id` 和查阅员令牌 `viewer_token` 下载交班单。

```bash
curl --fail-with-body -sS \
  -H "Authorization: Bearer ${viewer_token}" \
  "http://127.0.0.1:19436/api/runs/${run_id}/export/sorting_queue" \
  -o sorting-queue.csv
```

## 本地校验

领域入口为 `backend/src/domain/project.py`，由台账及后台计算服务调用。共享角色、运行状态和签核决定定义于 `backend/src/constants/workflow.py`，用于认证、运行查询和签核。测湿样本分区定义于 `backend/src/domain/records.py`，用于导入校验、模型训练、留出诊断和现货评分。

从本批根目录运行独立校验。脚本使用 Python 3.11+ 标准库，可读取实际领域代码与 CSV 序列化实现。

```bash
python3 test-artifacts/yangyang-pallet-mixture/tests/oracle.py -v
```

[业务剧本、CSV 示例与算法口径](BUSINESS.md)
