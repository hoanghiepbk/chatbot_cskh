import { PlainText } from './PlainText'

// Live "typing"-style indicator driven by SSE `status` events.
export function StatusIndicator({ text }: { text: string }) {
  return (
    <div className="status-indicator" aria-live="polite">
      <span className="typing-dots" aria-hidden>
        <i />
        <i />
        <i />
      </span>
      <PlainText text={text} />
    </div>
  )
}
