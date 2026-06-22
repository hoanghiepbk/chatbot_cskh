import { useState } from 'react'
import { isValidVnPhone, startChat } from '../api'
import type { StartResponse } from '../types'

// Screen 1 — phone gate. Validates a VN number client-side, then POST
// /chat/start to get { conversation_id, greeting }.
export function PhoneGate({ onStart }: { onStart: (res: StartResponse) => void }) {
  const [phone, setPhone] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function submit() {
    setError(null)
    if (!isValidVnPhone(phone)) {
      setError('Số điện thoại chưa đúng định dạng. Ví dụ: 0901 000 001')
      return
    }
    setLoading(true)
    try {
      const res = await startChat(phone.trim())
      onStart(res)
    } catch (e) {
      setError(
        e instanceof Error
          ? `Không kết nối được trợ lý (${e.message}). Anh/chị thử lại giúp mình nhé.`
          : 'Có lỗi xảy ra, anh/chị thử lại nhé.',
      )
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="gate">
      <div className="gate-card">
        <div className="brand">🔧 XeCare</div>
        <h1 className="gate-title">Chào anh/chị 👋</h1>
        <p className="gate-sub">
          Nhập số điện thoại để bắt đầu trò chuyện với trợ lý chăm sóc khách hàng XeCare.
        </p>
        <input
          className="gate-input"
          type="tel"
          inputMode="tel"
          placeholder="Số điện thoại (vd: 0901 000 001)"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void submit()
          }}
          aria-label="Số điện thoại"
          autoFocus
        />
        {error && (
          <div className="gate-error" role="alert">
            {error}
          </div>
        )}
        <button
          className="btn btn-primary btn-block"
          onClick={() => void submit()}
          disabled={loading}
        >
          {loading ? 'Đang kết nối…' : 'Bắt đầu'}
        </button>
        <p className="gate-privacy">🔒 Số điện thoại dùng để tra cứu hồ sơ xe của bạn.</p>
      </div>
    </div>
  )
}
