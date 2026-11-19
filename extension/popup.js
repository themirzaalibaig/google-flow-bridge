document.addEventListener('DOMContentLoaded', async () => {
  const statusEl = document.getElementById('status');
  const checkBtn = document.getElementById('checkBtn');

  async function checkServer() {
    statusEl.textContent = 'Checking server...';
    statusEl.className = 'status-badge status-disconnected';
    try {
      const res = await fetch('http://127.0.0.1:8765/health', { signal: AbortSignal.timeout(2000) });
      if (res.ok) {
        const data = await res.json();
        if (data.status === 'healthy') {
          statusEl.textContent = 'Connected (127.0.0.1:8765)';
          statusEl.className = 'status-badge status-connected';
          return;
        }
      }
    } catch (e) {}
    statusEl.textContent = 'Server Offline (127.0.0.1:8765)';
    statusEl.className = 'status-badge status-disconnected';
  }

  checkBtn.addEventListener('click', checkServer);
  await checkServer();
});
