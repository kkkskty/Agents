/** 解析后端「待确认订单」纯文本，供卡片化展示 */

export type OrderConfirmSection = {
  title: string
  body: string
}

export type ParsedOrderConfirmation = {
  intro: string | null
  sections: OrderConfirmSection[]
  footer: string
}

export function tryParseOrderConfirmation(content: string): ParsedOrderConfirmation | null {
  if (!content.includes('【待确认的订单信息】')) return null

  const footerMatch = content.match(/请确认是否执行[\s\S]+$/)
  const footer = footerMatch ? footerMatch[0].trim() : ''
  let main = footerMatch ? content.slice(0, footerMatch.index).trimEnd() : content

  let intro: string | null = null
  if (/^已收集必要信息。/m.test(main)) {
    intro = '已收集必要信息'
    main = main.replace(/^已收集必要信息。\s*\n*/m, '').trim()
  }

  main = main.replace(/^【待确认的订单信息】\s*\n*/m, '').trim()

  const segments = main.split(/\n(?=【)/).filter((s) => s.trim())
  const sections: OrderConfirmSection[] = []
  for (const seg of segments) {
    const m = seg.match(/^【([^】]+)】\s*([\s\S]*)$/m)
    if (m) {
      sections.push({ title: m[1].trim(), body: m[2].trim() })
    }
  }

  if (sections.length === 0) return null
  return { intro, sections, footer }
}

export function parseProductLines(body: string): string[] {
  return body
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l.startsWith('·'))
    .map((l) => l.replace(/^·\s*/, '').trim())
    .filter(Boolean)
}

export function parseKvLines(body: string): Array<{ label: string; value: string }> {
  const rows: Array<{ label: string; value: string }> = []
  for (const line of body.split('\n')) {
    const t = line.trim()
    if (!t) continue
    const m = t.match(/^([^：:]+)[：:](.+)$/)
    if (m) rows.push({ label: m[1].trim(), value: m[2].trim() })
  }
  return rows
}
