import type { CSSProperties } from 'react'

// Render ANY agent/staff/customer text as PLAIN TEXT. React escapes string
// children, so `<script>` or markdown injection renders literally and never
// executes. Even the agent's own reply goes through this — no
// dangerouslySetInnerHTML, no markdown renderer on any message content.
// Line breaks are preserved by the bubble's `white-space: pre-wrap`.
export function PlainText({
  text,
  className,
  style,
}: {
  text?: string | null
  className?: string
  style?: CSSProperties
}) {
  return (
    <span className={className} style={style}>
      {text ?? ''}
    </span>
  )
}
