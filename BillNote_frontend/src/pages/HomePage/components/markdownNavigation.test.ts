import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ReactMarkdown from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkMath from 'remark-math'

import {
  extractMarkdownHeadings,
  getCenteredOutlineScrollTop,
  getCollapsedOutlineHeadings,
  normalizeHeadingKey,
  normalizeMarkdownForRendering,
} from './markdownNavigation.ts'

test('centers the active lower chapter when the outline opens', () => {
  assert.equal(getCenteredOutlineScrollTop(720, 32, 240, 1000), 616)
  assert.equal(getCenteredOutlineScrollTop(900, 32, 240, 1000), 760)
})

test('normalizes model-generated LaTeX delimiters outside code fences', () => {
  const markdown = [
    '价格为 \\(0.8\\) 倍。',
    '',
    '\\[',
    '\\text{倍率}=0.8',
    '\\]',
    '',
    '```text',
    '\\[literal\\]',
    '```',
  ].join('\n')

  assert.equal(
    normalizeMarkdownForRendering(markdown),
    [
      '价格为 $0.8$ 倍。',
      '',
      '$$',
      '\\text{倍率}=0.8',
      '$$',
      '',
      '```text',
      '\\[literal\\]',
      '```',
    ].join('\n')
  )
})

test('matches generated TOC anchors to rendered headings after marker replacement', () => {
  const tocAnchor = '#skills-的触发分享与安装-content-0802'
  const renderedHeading = 'Skills 的触发、分享与安装 原片（08:02）'

  assert.equal(normalizeHeadingKey(tocAnchor), normalizeHeadingKey(renderedHeading))
})

test('removes only orphan stars immediately after origin links', () => {
  const markdown = [
    '## 第一章 [原片 @ 08:02](https://example.com?t=482)*',
    '## 第二章 [原片 @ 09:03](https://example.com?t=543)\\*',
    '正常的 *强调* 与普通链接 [文档](https://example.com)* 保留。',
    '`[原片 @ 10:00](https://example.com)*`',
  ].join('\n')

  assert.equal(
    normalizeMarkdownForRendering(markdown),
    [
      '## 第一章 [原片 @ 08:02](https://example.com?t=482)',
      '## 第二章 [原片 @ 09:03](https://example.com?t=543)',
      '正常的 *强调* 与普通链接 [文档](https://example.com)* 保留。',
      '`[原片 @ 10:00](https://example.com)*`',
    ].join('\n')
  )
})

test('normalized display math is rendered by KaTeX', () => {
  const html = renderToStaticMarkup(
    createElement(
      ReactMarkdown,
      { remarkPlugins: [remarkMath], rehypePlugins: [rehypeKatex] },
      normalizeMarkdownForRendering('\\[\n\\text{倍率}=0.8\n\\]')
    )
  )

  assert.match(html, /class="katex-display"/)
  assert.doesNotMatch(html, /^<p>/)
})

test('extracts h2 and h3 headings for the floating outline and excludes the TOC title', () => {
  const markdown = [
    '# 标题',
    '## 目录',
    '## 第一章 *Content-[00:06]*',
    '### 细节',
    '#### 不显示',
    '```md',
    '## 代码块内标题',
    '```',
  ].join('\n')

  assert.deepEqual(extractMarkdownHeadings(markdown), [
    { depth: 2, title: '第一章', key: '第一章', occurrence: 0 },
    { depth: 3, title: '细节', key: '细节', occurrence: 0 },
  ])
})

test('collapsed outline keeps every major chapter and excludes minor headings', () => {
  const headings = [
    { depth: 2 as const, title: '第一章', key: '第一章', occurrence: 0 },
    ...Array.from({ length: 24 }, (_, index) => ({
      depth: 3 as const,
      title: `小节 ${index + 1}`,
      key: `小节${index + 1}`,
      occurrence: 0,
    })),
    { depth: 2 as const, title: '最后一章', key: '最后一章', occurrence: 0 },
  ]

  assert.deepEqual(
    getCollapsedOutlineHeadings(headings).map(heading => heading.title),
    ['第一章', '最后一章']
  )
})
