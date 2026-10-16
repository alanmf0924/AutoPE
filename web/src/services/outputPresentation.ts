export type OutputPresentation = {
  finalAnswer: string
  hasThinkLeak: boolean
  isStructuredAnswer: boolean
  needsRawInspection: boolean
}

const THINK_TAG = /<\s*\/?\s*think(?:\s|>)/i
const LEADING_THINK_BLOCK = /^\s*<\s*think(?:\s[^>]*)?>[\s\S]*?<\s*\/\s*think\s*>\s*/i

function extractAnswerField(text: string): string {
  try {
    const parsed: unknown = JSON.parse(text)
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      return ''
    }
    const answer = (parsed as Record<string, unknown>).answer
    return typeof answer === 'string' ? answer.trim() : ''
  } catch {
    return ''
  }
}

export function presentModelOutput(rawText: string): OutputPresentation {
  const hasThinkLeak = THINK_TAG.test(rawText)
  const visibleText = rawText.replace(LEADING_THINK_BLOCK, '').trim()
  if (!visibleText || THINK_TAG.test(visibleText)) {
    return { finalAnswer: '', hasThinkLeak, isStructuredAnswer: false, needsRawInspection: Boolean(rawText.trim()) }
  }

  if (visibleText.startsWith('{')) {
    const answer = extractAnswerField(visibleText)
    return { finalAnswer: answer, hasThinkLeak, isStructuredAnswer: Boolean(answer), needsRawInspection: !answer }
  }

  return { finalAnswer: visibleText, hasThinkLeak, isStructuredAnswer: false, needsRawInspection: false }
}
