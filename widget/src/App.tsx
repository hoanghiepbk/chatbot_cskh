import { useState } from 'react'
import './App.css'
import { Chat } from './screens/Chat'
import { PhoneGate } from './screens/PhoneGate'
import type { StartResponse } from './types'

function App() {
  // conversation_id lives ONLY in React state (no localStorage/sessionStorage):
  // a refresh starts a fresh session — accepted for the demo (TIP-014w note).
  const [session, setSession] = useState<StartResponse | null>(null)

  return (
    <div className="app">
      {session ? (
        <Chat conversationId={session.conversation_id} greeting={session.greeting} />
      ) : (
        <PhoneGate onStart={setSession} />
      )}
    </div>
  )
}

export default App
