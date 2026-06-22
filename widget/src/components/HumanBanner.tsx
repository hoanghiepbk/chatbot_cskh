// Shown while a human staff member has taken over the conversation.
export function HumanBanner() {
  return (
    <div className="human-banner" role="status">
      <span aria-hidden>👩‍🔧</span> Nhân viên XeCare đang hỗ trợ bạn trực tiếp.
    </div>
  )
}
