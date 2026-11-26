# 验收记录：库位仪表偏差校准与增量切换

## 变更范围

| 模块 | 文件 | 说明 |
|---|---|---|
| 校准领域 | `backend/src/domain/calibration.py` | 偏差区间、修订号裁决、快照与变更库位 |
| 区间判定 | `backend/src/domain/sorting.py` | 区间分段评分、`calibration_span`、按区间最差点判定距离/后验 |
| 编排 | `backend/src/domain/project.py` | `compute` 全量计算与 `resume` 只重算变动库位 |
| 报告 | `backend/src/domain/reports.py` | 校准列、库位诊断的生效修订号 |
| 持久化 | `backend/src/models/calibration.py` | 追加校准表、运行冻结切片、台账当前版本 |
| 服务 | `backend/src/services/calibrations.py`、`services/runs.py`、`services/reviews.py`、`services/execution.py` | 录入、增量重算、签核切换、过期拦截 |
| 接口 | `backend/src/routes/calibrations.py`、`routes/runs.py`、`routes/datasets.py` | 校准 API、当前分拣单与稳定下载 |
| 文档 | `BUSINESS.md`、`README.md` | 业务口径与接口说明 |

## 验证

```bash
python3 test-artifacts/yangyang-pallet-mixture/tests/oracle.py -v
```

23 项通过。覆盖：

- 演示台账 144/48/19 分区与 BIC 958.488 / 767.715 / 778.290、成分中心 8.415% 与 21.049%。
- 未配置校准时待分拣清单与点规则逐件一致（9 件复测），列结构不变。
- 校准修订号乱序到达取最大；旧修订号晚到不覆盖；同修订号内容冲突拒绝，相同内容幂等。
- 偏差范围/顺序、修订号边界、库位编号校验。
- 区间整体在同一组时自动分拣；跨组转 `calibration_span`；两端同向而中间改道被识别；未训练库位照旧复测。
- 只重算变动库位时其余库位记录逐字段不变；增量结果与全量重算完全一致。
- 旧运行固定的修订号在最新校准到达后被识别为过期。
- 校准导出列顺序与 CSV 往返。

## 接口实测

用 `TestClient` + SQLite 跑通：

1. `POST /api/calibrations` 录入 `RACK-A` 修订 1、`RACK-B` 修订 2 与迟到的修订 1；响应 `accepted=3`、生效 `{'RACK-A': 1, 'RACK-B': 2}`。冲突修订返回 409。
2. `POST /api/runs` 生成运行并固定校准版本；worker 计算完成，清单追加区间与校准列。
3. 运行后补录 `RACK-B` 修订 3，`GET /api/runs/{id}/pending-changes` 返回 `["RACK-B"]`，对该运行签核返回 409。
4. `POST /api/runs {"recompute": true}` 触发增量重算，结果 `calibration.recomputed == ["RACK-B"]`；与全量重算对比，非 `RACK-B` 行完全不变。
5. 签核新运行返回 `switched: true`，`GET /api/datasets/{id}/downloads/sorting_queue` 与 `GET /api/runs/current` 指向新运行；旧运行详情与导出仍返回 200。
6. 先批准较新运行、再补写较旧运行的批准，`switch_release` 返回 `False`，当前版本保持较新运行。

## 已知边界

- 数值区间不是概率置信区间，只是仪表偏差的保守外包；正式放行仍以工场工艺标准为准。
- 增量重算要求上一运行的结果与冻结切片仍在库中；缺失时退回全量计算。
- 校准目前按库位全局生效，不按数据版本隔离；每次运行都会固定当时生效的修订号以便追溯。
