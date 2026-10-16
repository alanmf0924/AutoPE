const contractFieldNames: Record<string, string> = {
  response_contract: '回答要求',
  route: '处理路径',
  required_questions: '需要追问的内容',
  forbidden_claims: '不应作出的承诺',
  forbidden_actions: '不能执行的操作',
  required_tools: '必须执行的工具',
  forbidden_tools: '禁止执行的工具',
  required_response: '要求的回复内容',
}

export function explainCheckDetail(detail: string): string {
  return detail
    .replace(/\b(response_contract|route|required_questions|forbidden_claims|forbidden_actions|required_tools|forbidden_tools|required_response)\b/g, (field) => contractFieldNames[field] ?? field)
    .replaceAll('以下契约尚无可靠机器断言', '以下要求还不能由系统自动判断')
    .replaceAll('四维自动断言', '四项自动评分规则')
    .replaceAll('tool_calls 字段', '回答自述的工具调用')
}
