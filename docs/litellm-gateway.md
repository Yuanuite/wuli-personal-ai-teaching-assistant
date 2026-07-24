# LiteLLM 作为悟理模型网关

LiteLLM 适合作为悟理的“模型供应层”，不替代悟理自己的 Agent Gateway。

```text
教师端页面
→ 悟理 Agent Gateway：输入白名单、候选隔离、领域校验、教师复核
→ LiteLLM Proxy：多模型接入、别名、回退、限流、成本统计
→ Qwen / GPT / DeepSeek / Claude / Ollama / Azure / OpenRouter ...
```

悟理仍然拥有题目生命周期和安全边界；LiteLLM 只负责把不同模型统一成 OpenAI-compatible 接口。

这条路线不提供本地文件或 Skill 工具：LiteLLM 后面的模型只接收 Gateway 发送的结构化上下文并返回候选内容，不能自行读取仓库、调用脚本或启动浏览器。`analysis.generate` 本来就是无工具结构化任务，最适合走 LiteLLM；答案返修和可视化也可以走它，但确定性落盘、校验、仿真构建与浏览器检查由悟理后端完成。若任务必须让模型主动检查多个本地文件或调用 Skill 脚本，应改用 Claude/Codex 文件 Agent，或为目标 Agent Runtime 实现 JSON adapter。

## 最小本地配置

创建一个 LiteLLM `config.yaml`，把悟理需要的三档模型暴露为稳定别名：

```yaml
model_list:
  - model_name: wuli-economy
    litellm_params:
      model: deepseek/deepseek-chat
      api_key: os.environ/DEEPSEEK_API_KEY

  - model_name: wuli-standard
    litellm_params:
      model: openai/gpt-4.1-mini
      api_key: os.environ/OPENAI_API_KEY

  - model_name: wuli-expert
    litellm_params:
      model: openai/gpt-4.1
      api_key: os.environ/OPENAI_API_KEY
```

启动本机 LiteLLM Proxy：

```bash
litellm --config config.yaml --port 4000
```

如果 LiteLLM Proxy 设置了 master key，把它放在环境变量中：

```bash
export LITELLM_API_KEY="sk-..."
```

然后在教师端右上角“设置”中用“新增模型”手动添加这些别名，或从 `docs/model-registry.example.json` 复制到本机私有注册表。如 Proxy 设置了 master key，可直接在每个 LiteLLM 模型行填写 API Key；该 key 只保存到本机私有注册表，不会回显，也不会进入 GitHub。逐行点击“测试”，通过后保存，即可在“自动 / 经济 / 深度 / 自定义”模式里使用；未测试或测试失败的模型会置灰且不会被默认调用。

## 悟理注册表对应项

悟理只需要把 LiteLLM 当作 OpenAI-compatible API：

```json
{
  "id": "wuli-expert",
  "display_name": "悟理深度模型（LiteLLM）",
  "provider": "openai-compatible",
  "base_url": "http://127.0.0.1:4000/v1",
  "model": "wuli-expert",
  "api_key_env": "LITELLM_API_KEY",
  "remote": false,
  "model_tier": "expert",
  "capabilities": ["analysis.generate", "answer.revise", "visualization.model"]
}
```

`http://127.0.0.1:4000/v1` 是本机回环地址，不需要打开悟理远程 Agent 隐私门禁。若 LiteLLM 部署在远程服务器，则该模型会被视为远程模型，需要在 `student-error-library/config.json` 中设置 `privacy.allow_remote_agent=true`。

## 推荐职责分工

| 层级 | 职责 | 不负责 |
|---|---|---|
| 悟理 Agent Gateway | 文件白名单、候选隔离、物理/答案验证、教师复核、交付门禁 | 多供应商负载均衡、供应商级限流 |
| LiteLLM Proxy | 模型别名、供应商适配、回退、重试、成本/限流 | 判断题目是否可发布、批准答案、修改知识库 |

## 为什么不要直接替换 Gateway

LiteLLM 不知道悟理的 `record.json`、`answer-review.json`、`physics-model.json`、公开发布门禁或学生隐私图像。即使 LiteLLM 可以调用很多模型，候选内容仍必须经过悟理 Gateway 的白名单、校验和教师复核后才能提升到正式条目。

## 后续可增强

- 在作业记录中展示 LiteLLM 返回的真实上游模型、token 和成本；
- 用 LiteLLM 的回退/冷却能力管理 429、超时和额度不足；
- 多教师或多班级场景下，用 LiteLLM virtual key / budget 做预算隔离；
- 本地端侧模型可通过 Ollama/vLLM 接入 LiteLLM，再由悟理统一调用。
