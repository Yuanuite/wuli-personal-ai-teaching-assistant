# Flash Physical Kernel：thinking 模式对照

在题目、MiMo 视觉事实、Physical Kernel 契约、5000-token 上限和两路并发完全相同的条件下：

| 模式 | 结构成功 | 中位耗时 | 最大耗时 | 严格目标正确 |
|---|---:|---:|---:|---:|
| thinking disabled | 8/8 | 19.622 s | 26.198 s | 7/27 |
| thinking enabled | 0/8 | 47.305 s（失败） | 51.305 s（失败） | 无候选可审核 |

thinking enabled 的每道题都用完 5000 completion tokens，返回正文为 0 字符；隐藏推理约 1.52–2.30 万字符。也就是说，模型在输出 JSON 前已经耗尽预算。

因此，本轮不能得出“thinking enabled 的物理分析更准确”，只能确定它与当前紧凑结构契约和 90 秒生产目标不兼容。评测没有提高 token 上限，也没有重复失败调用。

当前可执行判断：Flash 保持 thinking disabled；把正确性提升重点放在少量、必须明确作答的决定性物理状态断言及其失败关闭门禁，而不是依赖不可控的长隐藏推理。
