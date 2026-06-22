import type { Citation } from '../types'
import { PlainText } from './PlainText'

// Small "Nguồn: …" chips under an FAQ answer.
export function Citations({ items }: { items: Citation[] }) {
  if (!items.length) return null
  return (
    <div className="citations">
      {items.map((c, i) => (
        <span className="citation-chip" key={`${c.doc_id}-${i}`}>
          <span aria-hidden>📄</span> Nguồn: <PlainText text={c.heading} />
        </span>
      ))}
    </div>
  )
}
