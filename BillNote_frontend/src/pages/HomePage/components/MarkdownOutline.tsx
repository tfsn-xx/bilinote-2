import { useEffect, useMemo, useRef, useState } from 'react'
import { ListTree } from 'lucide-react'

import type { MarkdownHeading } from './markdownNavigation.ts'
import {
  getCenteredOutlineScrollTop,
  getCollapsedOutlineHeadings,
  normalizeHeadingKey,
} from './markdownNavigation.ts'

interface MarkdownOutlineProps {
  containerRef: React.RefObject<HTMLDivElement | null>
  headings: MarkdownHeading[]
  onExpandedChange?: (expanded: boolean) => void
}

function findHeading(container: HTMLElement, key: string, occurrence: number) {
  return Array.from(container.querySelectorAll<HTMLElement>('h2, h3')).filter(
    heading => normalizeHeadingKey(heading.textContent || '') === key
  )[occurrence]
}

export default function MarkdownOutline({
  containerRef,
  headings,
  onExpandedChange,
}: MarkdownOutlineProps) {
  const [hovered, setHovered] = useState(false)
  const [pinned, setPinned] = useState(false)
  const [activeKey, setActiveKey] = useState(
    headings[0] ? `${headings[0].key}:${headings[0].occurrence}` : ''
  )
  const [activeSectionKey, setActiveSectionKey] = useState(() => {
    const firstSection = headings.find(heading => heading.depth === 2)
    return firstSection ? `${firstSection.key}:${firstSection.occurrence}` : ''
  })
  const outlineNavRef = useRef<HTMLElement>(null)
  const outlineAlignmentUntilRef = useRef(0)
  const expanded = hovered || pinned
  const outlineHeadings = useMemo(() => headings, [headings])
  const collapsedHeadings = useMemo(
    () => getCollapsedOutlineHeadings(headings),
    [headings]
  )

  useEffect(() => {
    onExpandedChange?.(expanded)
  }, [expanded, onExpandedChange])

  useEffect(() => {
    if (!expanded) {
      outlineAlignmentUntilRef.current = 0
      return
    }
    if (outlineAlignmentUntilRef.current === 0) {
      outlineAlignmentUntilRef.current = performance.now() + 1000
    }
    if (performance.now() > outlineAlignmentUntilRef.current) return

    const frame = requestAnimationFrame(() => {
      const nav = outlineNavRef.current
      const activeItem = nav?.querySelector<HTMLElement>('[data-outline-active="true"]')
      if (!nav || !activeItem) return

      const navRect = nav.getBoundingClientRect()
      const itemRect = activeItem.getBoundingClientRect()
      const itemTop = itemRect.top - navRect.top + nav.scrollTop
      nav.scrollTop = getCenteredOutlineScrollTop(
        itemTop,
        itemRect.height,
        nav.clientHeight,
        nav.scrollHeight
      )
    })

    return () => cancelAnimationFrame(frame)
  }, [activeKey, expanded])

  useEffect(() => {
    const container = containerRef.current
    const viewport = container?.closest<HTMLElement>('[data-radix-scroll-area-viewport]')
    if (!container || !viewport || outlineHeadings.length === 0) return

    let frame = 0
    const updateActive = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const viewportTop = viewport.getBoundingClientRect().top
        let current = outlineHeadings[0]
          ? `${outlineHeadings[0].key}:${outlineHeadings[0].occurrence}`
          : ''
        let currentSection = collapsedHeadings[0]
          ? `${collapsedHeadings[0].key}:${collapsedHeadings[0].occurrence}`
          : ''
        for (const item of outlineHeadings) {
          const element = findHeading(container, item.key, item.occurrence)
          if (element && element.getBoundingClientRect().top <= viewportTop + 120) {
            current = `${item.key}:${item.occurrence}`
            if (item.depth === 2) currentSection = current
          }
        }
        setActiveKey(current)
        setActiveSectionKey(currentSection)
      })
    }

    updateActive()
    viewport.addEventListener('scroll', updateActive, { passive: true })
    window.addEventListener('resize', updateActive)
    return () => {
      cancelAnimationFrame(frame)
      viewport.removeEventListener('scroll', updateActive)
      window.removeEventListener('resize', updateActive)
    }
  }, [collapsedHeadings, containerRef, outlineHeadings])

  if (outlineHeadings.length === 0) return null

  const jumpTo = (heading: MarkdownHeading) => {
    const container = containerRef.current
    const target = container && findHeading(container, heading.key, heading.occurrence)
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    setActiveKey(`${heading.key}:${heading.occurrence}`)
    const headingIndex = outlineHeadings.indexOf(heading)
    const section = outlineHeadings
      .slice(0, headingIndex + 1)
      .reverse()
      .find(item => item.depth === 2)
    if (section) setActiveSectionKey(`${section.key}:${section.occurrence}`)
  }

  return (
    <aside
      className="absolute top-2 right-2 z-20 hidden lg:block"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocus={() => setHovered(true)}
      onBlur={event => {
        if (!event.currentTarget.contains(event.relatedTarget)) setHovered(false)
      }}
    >
      <div
        className={`overflow-hidden rounded-xl border border-neutral-200/80 bg-white/95 shadow-lg backdrop-blur transition-[width] duration-200 ${expanded ? 'w-72' : 'w-9'}`}
      >
        <button
          type="button"
          className="flex h-9 w-full items-center justify-end px-2 text-neutral-500 hover:text-neutral-900"
          aria-label={expanded ? '收起文章目录' : '展开文章目录'}
          aria-expanded={expanded}
          onClick={() => setPinned(value => !value)}
        >
          {expanded && <span className="mr-auto truncate pl-1 text-sm font-medium">文章目录</span>}
          <ListTree className="h-4 w-4 shrink-0" />
        </button>

        {expanded ? (
          <nav ref={outlineNavRef} className="max-h-[min(68vh,36rem)] overflow-y-auto border-t border-neutral-100 p-2" aria-label="文章目录">
            {outlineHeadings.map(heading => (
              <button
                key={`${heading.key}-${heading.occurrence}`}
                type="button"
                className={`block w-full rounded-md py-1.5 pr-2 text-left text-sm transition-colors ${heading.depth === 3 ? 'pl-5' : 'pl-2 font-medium'} ${activeKey === `${heading.key}:${heading.occurrence}` ? 'bg-blue-50 text-blue-700' : 'text-neutral-600 hover:bg-neutral-100 hover:text-neutral-900'}`}
                data-outline-active={activeKey === `${heading.key}:${heading.occurrence}` ? 'true' : undefined}
                onClick={() => jumpTo(heading)}
                title={heading.title}
              >
                <span className="line-clamp-2">{heading.title}</span>
              </button>
            ))}
          </nav>
        ) : (
          <div className="flex max-h-[min(68vh,36rem)] flex-col items-center gap-1.5 overflow-hidden border-t border-neutral-100 py-2" aria-hidden="true">
            {collapsedHeadings.map(heading => (
              <span
                key={`${heading.key}-${heading.occurrence}`}
                className={`block h-0.5 w-3 rounded-full transition-colors ${activeSectionKey === `${heading.key}:${heading.occurrence}` ? 'bg-blue-600' : 'bg-neutral-300'}`}
              />
            ))}
          </div>
        )}
      </div>
    </aside>
  )
}
