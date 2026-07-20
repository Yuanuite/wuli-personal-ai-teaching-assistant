#!/bin/bash
# 悟理一键同步：审批可视化 → 交付 → 生成PDF → 发布学生站 → push
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== 1. 处理所有待审批/交付的条目 ==="
python3 << 'PYEOF'
import sys, json
sys.path.insert(0, '.claude/skills/manage-student-error-library/scripts')
from pathlib import Path
from process_uploads import approve_visualization, finish, pipeline_state
from public_site import prepare_publication, publish_prepared

library = Path('student-error-library')
site = Path('student-site')

for d in sorted((library/'entries').iterdir()):
    if not d.is_dir(): continue
    ps = pipeline_state(d)
    state = ps.get('state')
    r = json.loads((d/'record.json').read_text()) if (d/'record.json').exists() else {}
    title = r.get('title', d.name)

    # 1. 待审批可视化 → 自动审批
    if state == 'needs-visualization-review':
        approve_visualization(library, d.name, 'teacher', '轨迹正确')
        finish(library, d.name, None, 'auto')
        state = 'delivered'

    # 2. 已交付 → 重新准备公开预览 + 发布
    if state == 'delivered':
        prep = prepare_publication(library, d.name, site)
        pdf = prep.get('pdf', {})
        pub = publish_prepared(library, d.name, 'teacher', '', site)
        print(f'✓ {title[:30]} pdf:{pdf.get("engine","?")} pub:{pub.get("status")}')
PYEOF

echo ""
echo "=== 2. Git 提交推送 ==="
git add -A
if git diff --cached --quiet; then
    echo "没有新变更，跳过提交"
else
    git commit -m "chore: 同步条目与公开站"
    git push
fi

echo ""
echo "=== 完成 ==="
