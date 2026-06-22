const HOTLINE = '1900 1234'

// High-visibility safety banner shown with an emergency reply. The agent's reply
// already carries detailed guidance; this is the prominent hotline CTA.
export function EmergencyBanner() {
  return (
    <div className="emergency-banner" role="alert">
      <div className="emergency-title">🚨 Tình huống khẩn cấp</div>
      <div className="emergency-body">
        Nếu có người bị thương, gọi <strong>115</strong> trước. Đưa người và xe vào nơi an
        toàn, bật đèn cảnh báo và đứng cách xa làn xe chạy.
      </div>
      <a className="emergency-hotline" href={`tel:${HOTLINE.replace(/\s/g, '')}`}>
        📞 Hotline cứu hộ 24/7: {HOTLINE}
      </a>
    </div>
  )
}
