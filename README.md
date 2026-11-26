# 回收托盘板材含水混料分拣

托盘再利用工场导入来货板件的测湿台账。后台按整批切分历史、留出和待分拣记录，用高斯混合模型估计相对干湿成分，并按后验概率、成分距离和库位覆盖情况生成分拣清单。库位换表后，分拣班沿用已复核的混合模型，按库位录入仪表偏差上下限，由读数减偏差得到含水区间再决定是否自动分拣。

| 模块 | 业务产出 |
|---|---|
| 来货板件测湿台账 | CSV 导入、板件唯一性与整批分区校验、台账 SHA-256 |
| 库位仪表偏差校准 | 按库位与修订号追加校准记录，乱序到达时取最大修订号 |
| 高斯混合拟合与 BIC 选型 | EM 候选模型、BIC、冻结成分和训练来源 |
| 库位对账与待分拣清单 | 区间评分、库位密度诊断、带原因的人工复测单 |
| 增量重算与当前分拣单 | 只重算变动库位、签核后一次性切换、旧单仍可下载 |

## 启动与账号

在项目目录执行以下命令。首次启动准备 `.env` 时可从 `.env.example` 复制，并核对 `BACKEND_PORT=19436`、`DB_PORT=17936`。`SEED_DEMO=true` 时启动加载演示台账，手工运行 seed 可核对行数和校验和。

```bash
docker compose build backend
docker compose up -d --no-build --wait
docker compose exec -T backend python -m scripts.seed_demo
```

| Compose 服务 | 用途 | 地址 |
|---|---|---|
| `backend` | Python 3.11 / FastAPI 登录、台账、校准、签核和导出 | `http://127.0.0.1:19436/api` |
| `worker` | 后台拟合、区间评分、增量重算和队列生成 | Compose 内网 |
| `db` | PostgreSQL 16 台账、校准、运行、签核和审计 | `127.0.0.1:17936` |
| `redis` | Redis 7 作业通知和 worker 心跳 | Compose 内网 |

Compose 项目名为 `yangyang-pallet-mixture`。OpenAPI 位于 `http://127.0.0.1:19436/api/openapi.json`。环境配置包括服务端口、`COMPOSE_PROJECT_NAME`、`JWT_SECRET`、`SEED_DEMO`，应用通过 `DATABASE_URL` 和 `REDIS_URL` 访问存储。

| 角色 | 邮箱 | 演示密码 | 权限 |
|---|---|---|---|
| 分析员 `analyst` | `analyst@demo.local` | `Demo-2611!` | 导入台账、录入校准、提交计算和增量重算、查看计算和下载已获批 CSV |
| 复核员 `admin` | `admin@demo.local` | `Demo-2611!` | 分析员权限、签核和审计查询 |
| 查阅员 `viewer` | `viewer@demo.local` | `Demo-2611!` | 查看台账和已获批运行、下载当前或指定运行的分拣单 |

## API 工作流

通过 `POST /api/auth/login` 提交 `email` 和 `password`。业务请求使用 `Authorization: Bearer <access_token>`。

| 操作 | 方法与路径 | 输入或状态 |
|---|---|---|
| 查看列与配置 | `GET /api/spec` | CSV 字段顺序、运行参数、校准字段和模块名称 |
| 导入测湿台账 | `POST /api/datasets` | JSON 对象包含 `name` 和完整 CSV 文本 `csv`，成功返回 201 |
| 查找演示台账 | `GET /api/datasets` | 台账名为 `20261125-回收托盘板材测湿.csv` |
| 录入库位校准 | `POST /api/calibrations` | `records` 每项含 `location`、`bias_lower_pct`、`bias_upper_pct`、`revision`；同库位同修订号内容冲突返回 409 |
| 查看校准现状 | `GET /api/calibrations` | 各库位生效修订号与偏差区间；`GET /api/calibrations/records` 查看全部追加记录及是否当前 |
| 提交后台任务 | `POST /api/runs` | 提交 `label`、`dataset_id` 和可选 `settings`，成功返回 202 和运行编号；`recompute: true` 或 `parent_run_id` 触发增量重算 |
| 查看结果 | `GET /api/runs/{id}` | `completed` 时读取 `result.tables`，`failed` 时读取 `error` |
| 查看待重算库位 | `GET /api/runs/{id}/pending-changes` | 返回本次运行后校准修订号已变化的库位 |
| 签核 | `POST /api/runs/{id}/review` | `admin` 提交 `{"decision":"approved","note":"已核对复测件与库位偏移"}`；校准已更新返回 409 |
| 下载指定运行 CSV | `GET /api/runs/{id}/export/{key}` | `approved` 后可按表键下载，地址不可变 |
| 下载当前分拣单 | `GET /api/datasets/{id}/downloads/{key}` | 指向该台账最新复核并切换的版本，未就绪时提供上一获批版本 |
| 查看审计 | `GET /api/audit` | `admin` 查询最近操作 |

| 表键 | 内容 |
|---|---|
| `sorting_queue` | 待分拣板件、含水区间、首选与次选成分、操作与复测原因 |
| `bic_candidates` | 候选成分数、起点收敛情况、训练对数似然和 BIC |
| `model_components` | 成分权重、均值、方差、训练批次及复测阈值 |
| `holdout_scoring` | 独立批逐板评分，供复核对账 |
| `location_diagnostics` | 按分区和库位聚合的批数、平均对数密度、复测比例与生效修订号 |

配置了库位校准后，板件表在原有列之后追加 `bias_lower_pct`、`bias_upper_pct`、`calibration_revision`、`interval_lower_pct`、`interval_upper_pct`、`interval_uniform`、`interval_groups`，`posterior`、`log_density`、`nearest_z` 改为区间口径。未配置校准的台账保持原列不变。

下面的命令用已获批运行编号 `run_id` 和查阅员令牌 `viewer_token` 下载交班单。

```bash
curl --fail-with-body -sS \
  -H "Authorization: Bearer ${viewer_token}" \
  "http://127.0.0.1:19436/api/runs/${run_id}/export/sorting_queue" \
  -o sorting-queue.csv
```

## 校准与重算口径

- 校准记录只追加。同一库位按最大 `revision` 取值，与到达顺序无关；晚到的旧修订号保留留档但不生效。同一修订号内容冲突的记录整条拒绝。
- 板件含水区间为 `[读数 − 偏差上限, 读数 − 偏差下限]`。只有整段区间按原规则落在同一相对湿度组才自动分拣；区间跨组（含两端同向而中间改道）转复测并记 `calibration_span`。未校准库位沿用单点读数，未训练库位照旧复测。
- 每次运行固定当时各库位的生效修订号。校准后续更新时，旧运行复核与切换返回 409，需按最新校准重算。
- 增量重算复用上一运行的模型与各库位冻结结果，只重算生效修订号变化的库位；各库位并发更正都在每次重算时按最新校准一并核对，结果与全量重算逐位一致。
- 复核签核与当前分拣单切换在同一事务完成。较旧的运行即使稍后被复核，也不会覆盖已切换的较新版本；旧运行始终可按运行编号查询和导出。

## 本地校验

领域入口为 `backend/src/domain/project.py`，由台账及后台计算服务调用。库位校准与修订裁决位于 `backend/src/domain/calibration.py`，区间判定位于 `backend/src/domain/sorting.py`。共享角色、运行状态和签核决定定义于 `backend/src/constants/workflow.py`，用于认证、运行查询和签核。测湿样本分区定义于 `backend/src/domain/records.py`，用于导入校验、模型训练、留出诊断和现货评分。

从本批根目录运行独立校验。脚本使用 Python 3.11+ 标准库，可读取实际领域代码与 CSV 序列化实现。

```bash
python3 test-artifacts/yangyang-pallet-mixture/tests/oracle.py -v
```

[业务剧本、CSV 示例与算法口径](BUSINESS.md)
