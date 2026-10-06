const $ = (selector) => document.querySelector(selector);
const token = $('meta[name="csrf-token"]').content;
let buttons = [];
let editing = null;
let busy = false;

async function api(path, body) {
  const mutation = body !== undefined;
  const response = await fetch(`/api/${path}`, {
    method: mutation ? 'POST' : 'GET',
    headers: mutation ? { 'Content-Type': 'application/json', 'X-CSRF-Token': token } : {},
    ...(mutation ? { body: JSON.stringify(body) } : {}),
  });
  const data = await response.json();
  if (!response.ok && data.success !== false) throw new Error(`Erro HTTP ${response.status}`);
  return data;
}

function showResult(data, command) {
  $('#stdout').textContent = data.stdout || '—';
  $('#stderr').textContent = data.stderr || '—';
  $('#stderr-block').hidden = !(data.stderr || '').trim();
  $('#operation-state').textContent = `${command}: ${data.success ? 'concluído' : 'falhou'} (código ${data.code})`;
  $('#operation-state').className = data.success ? 'success' : 'error';
  const feedback = $('dialog[open] .modal-feedback');
  if (feedback) {
    feedback.hidden = false;
    feedback.textContent = data.success ? (data.stdout || 'Salvo.') : (data.stderr || 'Não foi possível concluir.');
    feedback.className = `modal-feedback ${data.success ? 'success' : 'error'}`;
  }
}

async function refreshRepository() {
  const data = await api('git/branches');
  const select = $('#branch-select');
  select.replaceChildren(new Option('Selecione uma branch', ''));
  $('#current-branch').textContent = data.success ? (data.currentBranch || 'HEAD destacado') : 'Indisponível';
  if (data.success) {
    const branches = [...data.branches].sort((a, b) => (a === data.currentBranch ? -1 : b === data.currentBranch ? 1 : a.localeCompare(b)));
    for (const branch of branches) {
      select.add(new Option(branch === data.currentBranch ? `${branch} (atual)` : branch, branch));
    }
    $('#branch-name').placeholder = data.currentBranch ? `Ex.: ${data.currentBranch}` : 'Ex.: main';
  }
  const behind = await api('git/behind');
  const count = $('#behind-count');
  count.textContent = behind.success ? String(behind.count) : 'Indisponível';
  count.classList.toggle('pending', behind.success && behind.count > 0);
  $('#behind-details').hidden = behind.success;
  $('#behind-error').textContent = behind.stderr || '';
}

async function perform(label, task) {
  if (busy) return;
  busy = true;
  document.querySelectorAll('button:not([data-close-dialog]), input, select').forEach((control) => { control.disabled = true; });
  const feedback = $('dialog[open] .modal-feedback');
  if (feedback) {
    feedback.hidden = false;
    feedback.textContent = 'Salvando…';
    feedback.className = 'modal-feedback';
  }
  $('#operation-state').textContent = `Executando ${label}…`;
  $('#operation-state').className = '';
  try {
    await task();
  } catch (error) {
    showResult({ success: false, code: -1, stderr: `Falha de comunicação: ${error.message}` }, label);
  } finally {
    busy = false;
    document.querySelectorAll('button, input, select').forEach((control) => { control.disabled = false; });
  }
}

function execute(command, body) {
  return perform(command, async () => {
    const data = await api(`git/${command}`, command === 'status' ? undefined : (body || {}));
    showResult(data, body?.command || command);
    await refreshRepository();
  });
}

function cancelEdit() {
  editing = null;
  $('#button-form').reset();
  $('#save-button').textContent = 'Adicionar botão';
  $('#cancel-edit').hidden = true;
}

function renderButtons() {
  const container = $('#button-manager');
  const shortcuts = $('#custom-buttons');
  container.replaceChildren();
  shortcuts.replaceChildren();
  shortcuts.hidden = buttons.length === 0;
  $('#no-buttons').hidden = buttons.length > 0;
  buttons.forEach((button, index) => {
    const row = document.createElement('div');
    row.className = 'custom-button-row';
    const run = document.createElement('button');
    run.textContent = button.label;
    run.title = button.command;
    run.addEventListener('click', () => execute('console', { command: button.command }));
    shortcuts.append(run);
    const label = document.createElement('span');
    label.className = 'button-label';
    label.textContent = button.label;
    const command = document.createElement('code');
    command.textContent = button.command;
    const edit = document.createElement('button');
    edit.textContent = 'Editar';
    edit.className = 'secondary';
    edit.setAttribute('aria-label', `Editar ${button.label}`);
    edit.addEventListener('click', () => {
      editing = index;
      $('#button-label').value = button.label;
      $('#button-command').value = button.command;
      $('#save-button').textContent = 'Salvar edição';
      $('#cancel-edit').hidden = false;
      $('#button-label').focus();
    });
    const remove = document.createElement('button');
    remove.textContent = 'Excluir';
    remove.className = 'secondary';
    remove.setAttribute('aria-label', `Excluir ${button.label}`);
    remove.addEventListener('click', () => saveButtons(buttons.filter((_, item) => item !== index)));
    row.append(label, command, edit, remove);
    container.append(row);
  });
  if (busy) {
    [container, shortcuts].forEach((element) => element.querySelectorAll('button').forEach((button) => { button.disabled = true; }));
  }
}

function saveButtons(nextButtons) {
  return perform('Salvar botões', async () => {
    const data = await api('buttons', { buttons: nextButtons });
    showResult(data, 'Salvar botões');
    if (data.success) {
      buttons = data.buttons;
      cancelEdit();
      renderButtons();
    }
  });
}

document.querySelectorAll('[data-open-dialog]').forEach((button) => {
  button.addEventListener('click', () => {
    const dialog = document.getElementById(button.dataset.openDialog);
    dialog.querySelector('.modal-feedback').hidden = true;
    dialog.showModal();
  });
});
document.querySelectorAll('[data-close-dialog]').forEach((button) => {
  button.addEventListener('click', () => button.closest('dialog').close());
});
document.querySelectorAll('dialog').forEach((dialog) => {
  dialog.addEventListener('click', (event) => {
    const bounds = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right
        || event.clientY < bounds.top || event.clientY > bounds.bottom)) dialog.close();
  });
});

document.querySelectorAll('[data-command]').forEach((button) => {
  button.addEventListener('click', () => execute(button.dataset.command));
});
$('#branch-select').addEventListener('change', (event) => { $('#branch-name').value = event.target.value; });
$('#switch-form').addEventListener('submit', (event) => {
  event.preventDefault();
  execute('switch', { branch: $('#branch-name').value });
});
$('#console-form').addEventListener('submit', (event) => {
  event.preventDefault();
  execute('console', { command: $('#console-command').value });
});
$('#settings-form').addEventListener('submit', (event) => {
  event.preventDefault();
  perform('Salvar configuração', async () => {
    const data = await api('settings', { gitPath: $('#git-path').value, repoPath: $('#repo-input').value });
    showResult(data, 'Salvar configuração');
    if (data.success) {
      $('#repo-path').textContent = data.resolvedRepoPath;
      $('#branch-name').value = '';
      $('#settings-dialog').close();
      await refreshRepository();
    }
  });
});
$('#button-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const nextButtons = [...buttons];
  const button = { label: $('#button-label').value, command: $('#button-command').value };
  if (editing === null) nextButtons.push(button);
  else nextButtons[editing] = button;
  saveButtons(nextButtons);
});
$('#cancel-edit').addEventListener('click', cancelEdit);

perform('Carregar painel', async () => {
  const settings = await api('settings');
  if (!settings.success) { showResult(settings, 'Configuração'); return; }
  buttons = settings.settings.buttons;
  renderButtons();
  showResult(await api('git/status'), 'status');
  await refreshRepository();
});
