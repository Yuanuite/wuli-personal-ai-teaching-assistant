# MiMo–DeepSeek 一致性收口最终验收（C6.3）

> 状态：`provisional`（功能门禁、真实 MiMo 合成图调用和干净克隆全链通过；静态图 warning → 单次软 Patch 的自动证据及维护者签署尚未完成）
> 计划版本：`wuli-mimo-deepseek-consistency-closure-v1`
> 基线提交：`026b104` → 收口提交：`b1277a1`、`d3bf060`、`55f1aab`、`0cffbea`
> 验收日期：2026-08-02

## 1. 目标达成矩阵（C-T1–C-T11）

| ID | 目标 | 状态 | 证据 |
|---|---|---|---|
| C-T1 | 提交自包含 | ✅ | `docs/reports/mimo-deepseek-closure-dependency-baseline-v1.json` 坐实 026b104 不自包含；归位 19 个运行模块+测试夹具后，`git archive HEAD` 解包 import `server/agent_gateway/entry_visual_extract/route_snapshot/diagram_application/visual_application` 全通（C1.3）|
| C-T2 | 视觉入口同源 | ✅ | `visual_application.run_visual_extract()` 被网页 `run-upload` 与薄 CLI 共用，产出同形 `VisualExtractOutcome.v1`；visual-web-clear 与 visual-cli-clear E2E 均通过且关键字段一致 |
| C-T3 | 原图调用唯一 | ✅ | source.clean 移除 `vision_images/requires_vision`，Gateway `_maybe_vision_preprocess` 删除；`test_agent_gateway.SourceCleanNoVisionTest` 断言任务无视觉字段；同 fingerprint 只经一次 visual.extract |
| C-T4 | 调用可审计 | ✅ | 私有 `visual-extract-request.json` 账本（schema/模型/provider/耗时/输入输出指纹/失败分类，脱敏无 key/data URL）；`test_visual_application` 断言脱敏 |
| C-T5 | 路由不可漂移 | ✅ | `wuli.route-snapshot.v1` 入队冻结、执行前 digest 校验失败关闭；job public 含快照；`test_route_snapshot` 4 项 + `test_agent_http` route_snapshot 断言；runtime-route-rollback E2E 验证 stale 可见、死端点失败关闭 |
| C-T6 | 静态图入口可达 | ✅ | `POST /api/entries/<id>/build-diagram` + `entry_action.py build-diagram` 共用 `diagram_application.build_diagram()`；static-diagram-collaboration E2E 通过（网页+CLI 同服务）|
| C-T7 | MiMo 图后复核真实 | ✅ | `diagram_visual_review.py` 契约测试通过；2026-08-02 使用无隐私合成静态图真实调用 `mimo-v2.5-flash`，返回 `passed` 与零建议；软评审不拥有物理真源或批准权限 |
| C-T8 | Patch 严格有界 | ⚠️ | 硬门 JSON-Patch 至多一次已有测试；代码将软补丁限制为一次，但现有静态图 E2E 的 mock 返回 visual-facts 契约，场景可在 `web_soft_review=failed` 时整体通过，尚未执行 warning → 单次软 Patch → 全硬门复验分支 |
| C-T9 | E2E 对称且失败关闭 | ✅ | 5 条收口 E2E 全通过（visual-web-clear / visual-cli-clear / visual-blurred-fail-closed / static-diagram-collaboration / runtime-route-rollback）；lifecycle/publication/claim-evidence 亦通过；仅写临时库与 test-results |
| C-T10 | 测试不假绿 | ✅ | `run_tests.py --strict`（missing/skipped 计失败）+ `--exclude`；`--all --strict` 70/70；干净克隆 18/18 |
| C-T11 | 文档行为一致 | ✅ | teacher-console-api（build-diagram/route_snapshot/账本）、agent-gateway（trait/路由快照）、visual-review-integration（registry 默认/探针/薄 CLI）、operator-runbook（--strict/entry_action/探针）、CHANGES 同步；graphify 无新职责倒置 |

## 2. 干净克隆全链（C5.7）

```text
git archive HEAD → 解包临时目录
  → import server/agent_gateway/entry_visual_extract/route_snapshot/diagram_application/visual_application : OK
  → run_tests.py --strict : 18/18 通过
  → run_e2e.py --scenario visual-cli-clear : passed
正式知识库/output/student-site：零写入
```

## 3. 已知排除项（单独报告，不计入通过数）

| 项 | 原因 | 处置 |
|---|---|---|
| `visualization.e2e.mjs` token 期望 420 vs 实际 450 | 预存于未提交 W3/evidence 流的调用次数/usage 漂移，非本收口范围（交互仿真与 W3 策略排除）；未改动断言以免掩盖漂移 | 需 W3 流 owner 判定是流程多调用还是期望过期；收口不将其计入通过数 |

## 4. 外部服务状态

- 2026-08-02 已使用仓库外临时生成的无隐私合成 SVG/PNG 执行真实 MiMo 静态图软评审；
  运行身份为 `mimo-v2.5-flash` / 上游 `mimo-v2.5`，结果为 `passed`、零建议。
- `wuli.vision-probe.v1` 与 `run_diagram_visual_review` 使用生产同形 endpoint/图片/JSON
  契约；`vision_probe=failed` 会排除出视觉路由，外部端点不可用时失败关闭到人工复核。
- 此真实零建议结果不能替代 warning → 软 Patch 集成测试。

## 5. 维护者人工检查项（最终批准前）

1. 运行 `teacher-console/scripts/run_tests.py --strict` 与五条收口 E2E 复核；
2. 打开教师工作台 `/api/health` 查看运行身份与"服务需重启"横幅；
3. 对一道已批准答案的测试题执行 `build-diagram`（网页按钮或 CLI），确认图生成、
   MiMo 软评审记录与批准失效回答案复核；
4. 在网页上传一道含原图的新题，确认视觉提取走 registry 路由、停在
   `needs-source-review` 且无自动批准。

签署后，将本节状态从"待签署"改为"已签署：<姓名/角色>，<日期>"。
