# 黄金集自动评测修复计划

## 目标

把「真实失败」「Dify 调用错误」「待人工复核」分开呈现；保留原始 Promptfoo
契约，并让新版本黄金集能同时保存人工判断依据和可执行机器断言。

## 范围

1. 数据集用例新增只读 `expected_contract`，保存 `intent/action/evidence` 等原始契约。
2. Worker 明确写入 `passed`、`failed`、`manual_review`、`execution_error` 四种结果。
3. 报告新增自动通过、自动失败、待人工复核和调用错误计数；存在未判定用例时显示
   `pending_review`，而不是误报 NO-GO。
4. 人工标注回写该用例的最终通过/失败结果，并重新汇总报告。
5. 提供黄金集、Dify 输出契约和 DSPy 候选的提交格式。

## 不在本次自动完成的范围

- 不修改现有冻结数据集、历史响应或生产 Dify Prompt。
- 不把自然语言的 `expected_behavior` 猜成关键词断言。
- 不自动发布或自动应用 DSPy 候选到 Dify。

## 验收

- 无 `evaluation_rules` 的用例进入 `manual_review`，报告状态为 `pending_review`，且不计入失败数。
- 完整规则但输出不满足时，计为真实失败并保持 NO-GO。
- Dify 调用失败单独计数，不能被当成回答质量失败。
- 标注后，报告更新为人工通过或人工失败。
- 前后端构建、后端测试、迁移升级均通过。
