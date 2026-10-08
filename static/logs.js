const clearLogs = document.querySelector('#clear-logs');
const feedback = document.querySelector('#logs-feedback');

clearLogs.addEventListener('click', async () => {
  clearLogs.disabled = true;
  feedback.hidden = false;
  feedback.className = 'hint';
  feedback.textContent = 'Limpando logs…';
  try {
    const response = await fetch(clearLogs.dataset.url, {
      method: 'POST',
      headers: { 'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]').content },
    });
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.stderr || 'Não foi possível limpar os logs.');
    window.location.reload();
  } catch (error) {
    feedback.className = 'error';
    feedback.textContent = error.message;
    clearLogs.disabled = false;
  }
});
