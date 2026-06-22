import type { ChatMsg } from '../types'
import { Citations } from './Citations'
import { PlainText } from './PlainText'

export function MessageBubble({ msg }: { msg: ChatMsg }) {
  const isCustomer = msg.role === 'customer'
  const bubbleClass =
    'bubble ' +
    (isCustomer ? 'bubble-customer' : msg.role === 'staff' ? 'bubble-staff' : 'bubble-agent')

  return (
    <div className={`row ${isCustomer ? 'row-right' : 'row-left'}`}>
      <div className="bubble-wrap">
        {msg.role === 'staff' && <div className="sender-tag">Nhân viên</div>}
        <div className={bubbleClass}>
          <PlainText text={msg.text} />
        </div>
        {msg.citations && msg.citations.length > 0 && <Citations items={msg.citations} />}
      </div>
    </div>
  )
}
