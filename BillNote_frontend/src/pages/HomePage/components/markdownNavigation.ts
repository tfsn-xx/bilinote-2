export interface MarkdownHeading {
  depth: 2 | 3
  title: string
  key: string
  occurrence: number
}

const CONTENT_MARKER_RE = /\*?Content-(?:\[\d{2}:\d{2}\]|\d{2}:\d{2})\*?/gi
const ORIGIN_LINK_RE = /\[原片\s*@\s*\d{2}:\d{2}\]\([^)]*\)/gi
const ORIGIN_TEXT_RE = /原片(?:\s*@|[（(])?\s*\d{2}[:：]?\d{2}[）)]?/gi
type FenceState = { char: string; length: number } | null

function safeDecode(value: string) {
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

export function cleanHeadingTitle(value: string) {
  return value
    .replace(CONTENT_MARKER_RE, '')
    .replace(ORIGIN_LINK_RE, '')
    .replace(ORIGIN_TEXT_RE, '')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/[*_~`]/g, '')
    .trim()
}

export function normalizeHeadingKey(value: string) {
  return cleanHeadingTitle(safeDecode(value.replace(/^#/, '')))
    .replace(/(?:^|-)content-?\d{4}(?:$|-)/gi, '')
    .replace(/[\p{P}\p{S}\s]/gu, '')
    .toLocaleLowerCase()
}

function transformOutsideInlineCode(line: string) {
  return line
    .split(/(`+[^`]*`+)/g)
    .map((part, index) => {
      if (index % 2 === 1) return part
      return part
        .replace(
          /(\[原片\s*@\s*\d{2}:\d{2}\]\([^)]+\))\\?\*(?=\s|$)/g,
          '$1'
        )
        .replace(/\\\[([^\n]*?)\\\]/g, '$$$$\n$1\n$$$$')
        .replace(/\\\((.+?)\\\)/g, '$$$1$$')
    })
    .join('')
}

function updateFenceState(line: string, current: FenceState) {
  const match = line.match(/^\s*(`{3,}|~{3,})/)
  if (!match) return { isFenceLine: false, next: current }

  const marker = match[1]
  if (!current) {
    return { isFenceLine: true, next: { char: marker[0], length: marker.length } }
  }
  if (marker[0] === current.char && marker.length >= current.length) {
    return { isFenceLine: true, next: null }
  }
  return { isFenceLine: true, next: current }
}

export function normalizeMarkdownForRendering(markdown: string) {
  let fence: FenceState = null

  return markdown
    .split('\n')
    .map(line => {
      const fenceUpdate = updateFenceState(line, fence)
      fence = fenceUpdate.next
      if (fenceUpdate.isFenceLine) return line

      if (fence) return line
      if (/^\s*\\\[\s*$/.test(line) || /^\s*\\\]\s*$/.test(line)) return '$$'
      return transformOutsideInlineCode(line)
    })
    .join('\n')
}

export function extractMarkdownHeadings(markdown: string): MarkdownHeading[] {
  const headings: MarkdownHeading[] = []
  const occurrences = new Map<string, number>()
  let fence: FenceState = null

  for (const line of markdown.split('\n')) {
    const fenceUpdate = updateFenceState(line, fence)
    fence = fenceUpdate.next
    if (fenceUpdate.isFenceLine) continue
    if (fence) continue

    const match = line.match(/^(#{2,3})\s+(.+?)\s*#*\s*$/)
    if (!match) continue
    const title = cleanHeadingTitle(match[2])
    const key = normalizeHeadingKey(title)
    if (!key || key === '目录' || key === 'toc') continue
    const occurrence = occurrences.get(key) || 0
    occurrences.set(key, occurrence + 1)
    headings.push({ depth: match[1].length as 2 | 3, title, key, occurrence })
  }

  return headings
}

export function getCollapsedOutlineHeadings(headings: MarkdownHeading[]) {
  return headings.filter(heading => heading.depth === 2)
}

export function getCenteredOutlineScrollTop(
  itemTop: number,
  itemHeight: number,
  viewportHeight: number,
  scrollHeight: number
) {
  const centeredTop = itemTop - (viewportHeight - itemHeight) / 2
  return Math.min(
    Math.max(centeredTop, 0),
    Math.max(scrollHeight - viewportHeight, 0)
  )
}
