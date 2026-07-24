# 教师工作台 E2E

这套测试为每个场景创建独立临时目录，启动真实 `ThreadingHTTPServer`，再通过 Playwright 操作教师端 UI。当前有 3 条可执行 E2E：

```text
基础交付：上传 → 题干复核 → Agent 解析 → 答案批准 → finish → 评价与质量诊断
可视化：基础复核 → Agent 物理模型 → 确定性 HTML/ZIP 构建 → 浏览器操作控件 → 重新批准答案 → 批准可视化 → 交付
公开发布：交付 → 题图遮挡/确认 → 公开草稿 → 浏览器预览 → 隐私确认 → 本地 student-site 发布与泄漏扫描
```

外部不确定边界中，OCR 被显式关闭并由浏览器填写教师校对稿，Agent 被确定性 JSON adapter 替代；
HTTP、入库、后台任务、候选提升、生命周期门禁、文件导出和浏览器交互均使用生产实现。测试不会读取或修改真实的 `error-collection/`、
`student-error-library/`、`output/` 或 `student-site/`。可视化场景会运行生产仿真构建器和浏览器运行时检查；公开发布场景会确认原图字节未变，
并扫描公开树中是否出现条目 ID、原始文件名、教师版、内部记录或本地路径。

教师在真实工作台里处理、复核或交付题目不会调用本目录的 runner，也不会把一次操作录制或追加成测试。
只有维护者执行下面的命令，或 CI 显式启动时才运行 E2E；临时场景结束后不会形成正式题库条目。

交付后会：

1. 独立检查 `delivery-manifest.json` 和白名单产物；
2. 使用生产 `evaluator.py` 生成的 `evaluation.json` 核对领域门禁；
3. 运行 `pipeline_quality_eval.py`，把内容、流程、Token 和耗时诊断写入测试报告。

其中 `evaluator.py` 和 `pipeline_quality_eval.py` 是测试 oracle（结果判定与诊断），不是 UI/API 驱动层。

运行：

```bash
npm ci
npx playwright install chromium
npm run test:e2e
```

报告写入 `test-results/e2e/`。失败时临时工作区会复制到该目录，便于定位生命周期断点。
单独调试某个场景：

```bash
npm run test:e2e -- --scenario visualization
npm run test:e2e -- --scenario publication
```
