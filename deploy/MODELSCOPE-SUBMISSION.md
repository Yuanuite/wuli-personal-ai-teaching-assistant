# 悟理魔搭创空间提交清单

## 创建时填写

| 字段 | 建议值 |
|---|---|
| 英文名称 | `wuli-ai-teaching-assistant` |
| 中文名称 | `悟理·乡村物理学习助理` |
| 可见性 | 公开 |
| SDK | Static |
| 许可证 | MIT |
| 简介 | 面向乡村课堂的端侧可信 AI 全流程教学助教平台 |
| 云资源 | 免费 CPU 或平台默认配置 |

## 需要上传

上传 `modelscope-static/` 目录中的全部文件，并保持文件名不变：

```text
README.md
LICENSE
index.html
teacher-publication.png
simulation.png
student-site.png
```

不要上传整个项目目录，也不要上传 `student-error-library/`、`error-collection/`、`output/`、本地配置、访问令牌或 API Key。

## 发布前核对

1. “空间文件”中能看到上述 6 个文件。
2. README 的 YAML 顶部包含 `deployspec.entry_file: index.html` 和 `license: MIT`。
3. 在“设置”中确认 SDK 为 Static、可见性为公开。
4. 点击“立即发布”或“启动”。
5. 页面启动后检查三张图片均可显示，GitHub 链接可打开。

`requirements.txt`、`packages.txt` 和 `configuration.json` 不属于这个纯静态创空间的必需文件，不要为了凑文件而添加。
