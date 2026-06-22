import { PlainText } from './PlainText'

// Stands out from normal bubbles: the confirm gate for a booking/cancel action.
// Confirm/Cancel POST /chat/{id}/confirm — the only path that runs a write tool.
export function ConfirmCard({
  summary,
  busy,
  onConfirm,
  onCancel,
}: {
  summary?: string
  busy: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <div className="confirm-card" role="group" aria-label="Xác nhận thao tác">
      <div className="confirm-title">✅ Xác nhận thao tác</div>
      <div className="confirm-summary">
        <PlainText text={summary ?? 'Anh/chị xác nhận thực hiện thao tác này?'} />
      </div>
      <div className="confirm-actions">
        <button className="btn btn-primary" onClick={onConfirm} disabled={busy}>
          Xác nhận
        </button>
        <button className="btn btn-ghost" onClick={onCancel} disabled={busy}>
          Hủy
        </button>
      </div>
    </div>
  )
}
