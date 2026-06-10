import "./App.css";

function App() {
  return (
    <main className="chat-container">
      <h1>XeCare Chat</h1>
      {/* Placeholder UI — phone login logic lands in TIP-014w */}
      <input
        type="tel"
        placeholder="Nhập số điện thoại để bắt đầu"
        disabled
        aria-label="Số điện thoại"
      />
    </main>
  );
}

export default App;
