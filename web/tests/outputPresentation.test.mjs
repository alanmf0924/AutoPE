import assert from 'node:assert/strict'
import test from 'node:test'

import { presentModelOutput } from '../src/services/outputPresentation.ts'

test('shows only the answer from a response with a leading think block', () => {
  const raw = '<think>内部推理，不应进入默认阅读区。</think>{"answer":"请先核对成分表。","evaluation":{"tool_calls":[]}}'
  assert.deepEqual(presentModelOutput(raw), {
    finalAnswer: '请先核对成分表。',
    hasThinkLeak: true,
    isStructuredAnswer: true,
    needsRawInspection: false,
  })
  assert.match(raw, /内部推理/)
})

test('keeps plain responses readable', () => {
  assert.deepEqual(presentModelOutput('请先说明肤质和预算。'), {
    finalAnswer: '请先说明肤质和预算。',
    hasThinkLeak: false,
    isStructuredAnswer: false,
    needsRawInspection: false,
  })
})

test('does not promote unclosed or embedded think content as the final answer', () => {
  for (const raw of [
    '<think>推理未闭合 {"answer":"看似正常"}',
    '{"answer":"<think>内部内容</think> 不可直接展示"}',
  ]) {
    const result = presentModelOutput(raw)
    assert.equal(result.finalAnswer, '')
    assert.equal(result.hasThinkLeak, true)
    assert.equal(result.needsRawInspection, true)
  }
})

test('does not invent an answer when structured output lacks a valid answer field', () => {
  assert.deepEqual(presentModelOutput('{"evaluation":{"intent":"clarification"}}'), {
    finalAnswer: '',
    hasThinkLeak: false,
    isStructuredAnswer: false,
    needsRawInspection: true,
  })
})
