const $ = (selector) => document.querySelector(selector);
const token = $('meta[name="csrf-token"]').content;
let buttons = [];
let editing = null;
let busy = false;
let projectId = null;
let currentSettings = null;
let addingProject = false;
let deletionChallenge = null;
let branchData = { branches: [], remoteBranches: [], currentBranch: '' };
let autoFetching = false;
let lastAutoFetch = 0;
const AUTO_FETCH_INTERVAL = 30000;

async function api(path, body) {
  const mutation = body !== undefined;
  const query = projectId ? `?projectId=${encodeURIComponent(projectId)}` : '';
  const response = await fetch(`/api/${path}${query}`, {
    method: mutation ? 'POST' : 'GET',
    headers: mutation ? { 'Content-Type': 'application/json', 'X-CSRF-Token': token } : {},
    ...(mutation ? { body: JSON.stringify(body) } : {}),
  });
  const text = await response.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error(`Resposta inesperada do servidor (HTTP ${response.status}).`);
  }
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
  const current = data.success ? (data.currentBranch || '') : '';
  const local = data.success ? data.branches : [];
  branchData = {
    branches: [...local].sort((a, b) => (a === current ? -1 : b === current ? 1 : a.localeCompare(b))),
    remoteBranches: data.success ? (data.remoteBranches || []) : [],
    currentBranch: current,
  };
  $('#current-branch').textContent = data.success ? (current || 'HEAD destacado') : 'Indisponível';
  if (data.success) {
    $('#branch-name').placeholder = current ? `Ex.: ${current}` : 'Ex.: main';
  }
  if ($('#switch-dialog').open) renderBranchList();
  const behind = await api('git/behind');
  const count = $('#behind-count');
  count.textContent = behind.success ? String(behind.count) : 'Indisponível';
  count.classList.toggle('pending', behind.success && behind.count > 0);
  $('#behind-details').hidden = behind.success;
  $('#behind-error').textContent = behind.stderr || '';
}

// Busca o remoto sem clique: ao abrir a página e ao voltar para a aba (com intervalo mínimo).
async function fetchFromRemote(force = false) {
  if (!projectId || busy || autoFetching || $('dialog[open]')) return;
  if (!force && Date.now() - lastAutoFetch < AUTO_FETCH_INTERVAL) return;
  return perform('Busca automática', async () => {
    autoFetching = true;
    lastAutoFetch = Date.now();
    const state = $('#auto-fetch-state');
    state.hidden = false;
    state.className = 'hint';
    state.textContent = 'Buscando referências do remoto…';
    let fetched = false;
    try {
      const data = await api('git/autofetch', {});
      fetched = data.success;
      if (data.success) {
        state.className = 'hint success';
        state.textContent = 'Referências remotas atualizadas automaticamente.';
      } else {
        showResult(data, 'Busca automática');
        const detail = (data.stderr || '').trim().split('\n')[0];
        state.className = 'hint error';
        state.textContent = `Busca automática falhou: ${detail || `código ${data.code}`}`;
      }
    } catch (error) {
      showResult({ success: false, code: -1, stderr: error.message }, 'Busca automática');
      state.className = 'hint error';
      state.textContent = `Busca automática falhou: ${error.message}`;
    } finally {
      autoFetching = false;
    }
    // Sem isto o stdout continuaria mostrando o status calculado antes da busca.
    if (fetched) showResult(await api('git/status'), 'status');
    await refreshRepository();
  });
}

async function perform(label, task) {
  if (busy) return;
  busy = true;
  document.querySelectorAll('button:not([data-close-dialog]), input, select').forEach((control) => { control.disabled = true; });
  const feedback = $('dialog[open] .modal-feedback');
  if (feedback) {
    feedback.hidden = false;
    feedback.textContent = `${label}…`;
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

async function refreshProjects() {
  const data = await api('projects');
  if (!data.success) throw new Error(data.stderr);
  const select = $('#project-select');
  select.replaceChildren();
  data.projects.forEach((project) => select.add(new Option(project.name, project.id)));
  if (!data.projects.some((project) => project.id === projectId)) projectId = data.projects[0]?.id || null;
  select.value = projectId || '';
}

function fillSettings(adding = false) {
  addingProject = adding;
  resetDeleteProject();
  $('#delete-project-area').hidden = adding || !projectId;
  $('#settings-title').textContent = adding ? 'Adicionar projeto' : `Configuração — ${currentSettings?.name || 'Projeto'}`;
  $('#save-settings').textContent = adding ? 'Adicionar projeto' : 'Salvar configuração';
  $('#project-name').value = adding ? '' : (currentSettings?.name || '');
  $('#git-path').value = adding ? 'git' : (currentSettings?.gitPath || 'git');
  $('#repo-input').value = adding ? '' : (currentSettings?.repoPath || '');
}

async function loadProject() {
  document.querySelectorAll('[data-project-only]').forEach((element) => { element.hidden = !projectId; });
  $('#no-projects').hidden = Boolean(projectId);
  $('#project-select').hidden = !projectId;
  const url = new URL(window.location.href);
  if (projectId) url.searchParams.set('projectId', projectId);
  else url.searchParams.delete('projectId');
  window.history.replaceState(null, '', url);
  currentSettings = null;
  buttons = [];
  branchData = { branches: [], remoteBranches: [], currentBranch: '' };
  cancelEdit();
  renderButtons();
  fillSettings();
  $('#repo-path').textContent = 'Carregando…';
  $('#buttons-title').textContent = 'Botões personalizados';
  $('#stdout').textContent = '—';
  $('#stderr-block').hidden = true;
  $('#current-branch').textContent = 'Carregando…';
  $('#behind-count').textContent = 'Carregando…';
  $('#behind-details').hidden = true;
  $('#auto-fetch-state').hidden = true;
  $('#branch-name').value = '';
  $('#console-command').value = '';
  lastAutoFetch = 0;
  if (!projectId) return;
  const data = await api('settings');
  if (!data.success) throw new Error(data.stderr);
  currentSettings = data.settings;
  $('#repo-path').textContent = data.resolvedRepoPath;
  $('#buttons-title').textContent = `Botões — ${currentSettings.name}`;
  buttons = currentSettings.buttons;
  cancelEdit();
  renderButtons();
  fillSettings();
  // Keep the selection in the URL: independent tabs never share an active project.
  showResult(await api('git/status'), 'status');
  await refreshRepository();
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

function renderBranchList() {
  const list = $('#branch-options');
  list.replaceChildren();
  let total = 0;
  for (const [title, names] of [['Locais', branchData.branches], ['Remotas', branchData.remoteBranches]]) {
    if (!names.length) continue;
    const heading = document.createElement('li');
    heading.className = 'branch-group';
    heading.textContent = title;
    list.append(heading);
    for (const name of names) {
      total += 1;
      const current = name === branchData.currentBranch;
      const option = document.createElement('button');
      option.type = 'button';
      option.className = current ? 'branch-option current' : 'branch-option';
      option.dataset.branch = name;
      option.textContent = name;
      if (current) {
        option.setAttribute('aria-label', `${name} (branch atual)`);
        const tag = document.createElement('span');
        tag.className = 'tag';
        tag.setAttribute('aria-hidden', 'true');
        tag.textContent = 'atual';
        option.append(tag);
      }
      option.addEventListener('click', () => chooseBranch(name));
      const item = document.createElement('li');
      item.append(option);
      list.append(item);
    }
  }
  if (!total) {
    const empty = document.createElement('li');
    empty.className = 'branch-empty';
    empty.textContent = 'Nenhuma branch encontrada. Use Fetch para atualizar as referências remotas.';
    list.append(empty);
  }
}

function openSwitchDialog() {
  const dialog = $('#switch-dialog');
  dialog.querySelector('.modal-feedback').hidden = true;
  $('#branch-name').value = '';
  renderBranchList();
  dialog.showModal();
  $('#branch-name').focus();
}

function chooseBranch(name) {
  $('#branch-name').value = name;
  submitSwitch();
}

function submitSwitch() {
  const branch = $('#branch-name').value.trim();
  if (!branch) { $('#branch-name').focus(); return; }
  return perform('troca de branch', async () => {
    const data = await api('git/switch', { branch });
    showResult(data, branch);
    if (data.success) {
      $('#branch-name').value = '';
      $('#switch-dialog').close();
    }
    await refreshRepository();
  });
}

document.querySelectorAll('[data-open-dialog]').forEach((button) => {
  button.addEventListener('click', () => {
    const dialog = document.getElementById(button.dataset.openDialog);
    if (dialog.id === 'settings-dialog') fillSettings();
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
$('#open-switch').addEventListener('click', openSwitchDialog);
$('#switch-form').addEventListener('submit', (event) => { event.preventDefault(); submitSwitch(); });
$('#console-form').addEventListener('submit', (event) => {
  event.preventDefault();
  execute('console', { command: $('#console-command').value });
});
$('#settings-form').addEventListener('submit', (event) => {
  event.preventDefault();
  perform('Salvar configuração', async () => {
    const data = await api(addingProject ? 'projects' : 'settings', {
      name: $('#project-name').value, gitPath: $('#git-path').value, repoPath: $('#repo-input').value,
    });
    showResult(data, 'Salvar configuração');
    if (data.success) {
      projectId = data.settings.id;
      $('#settings-dialog').close();
      await refreshProjects();
      await loadProject();
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

$('#add-project').addEventListener('click', () => {
  fillSettings(true);
  $('#settings-dialog .modal-feedback').hidden = true;
  $('#settings-dialog').showModal();
  $('#project-name').focus();
});
$('#delete-project').addEventListener('click', () => {
  if (!projectId || addingProject || busy) return;
  const left = Math.floor(Math.random() * 9) + 1;
  const right = Math.floor(Math.random() * 9) + 1;
  deletionChallenge = { projectId, answer: left + right };
  $('#delete-project-question').textContent = `Tem certeza que deseja excluir o projeto “${currentSettings?.name || 'Projeto'}”?`;
  $('#delete-project-sum').textContent = `Para confirmar, quanto é ${left} + ${right}?`;
  $('#delete-project-answer').value = '';
  $('#delete-project-error').hidden = true;
  $('#delete-project-form').hidden = false;
  $('#delete-project').setAttribute('aria-expanded', 'true');
  $('#delete-project-answer').focus();
});

function resetDeleteProject() {
  deletionChallenge = null;
  $('#delete-project-form').hidden = true;
  $('#delete-project-answer').value = '';
  $('#delete-project-error').hidden = true;
  $('#delete-project').setAttribute('aria-expanded', 'false');
}

$('#cancel-delete-project').addEventListener('click', () => {
  resetDeleteProject();
  $('#delete-project').focus();
});
$('#settings-dialog').addEventListener('close', resetDeleteProject);
$('#delete-project-form').addEventListener('submit', (event) => {
  event.preventDefault();
  if (busy || !deletionChallenge || deletionChallenge.projectId !== projectId) return;
  const answer = $('#delete-project-answer').value.trim();
  if (!/^\d+$/.test(answer) || Number(answer) !== deletionChallenge.answer) {
    $('#delete-project-error').textContent = 'Resultado incorreto. Confira a soma e tente novamente.';
    $('#delete-project-error').hidden = false;
    $('#delete-project-answer').focus();
    return;
  }
  return perform('Excluir projeto', async () => {
    const data = await api('projects/delete', {});
    showResult(data, 'Excluir projeto');
    if (!data.success) return;
    resetDeleteProject();
    $('#settings-dialog').close();
    await refreshProjects();
    await loadProject();
  });
});
$('#project-select').addEventListener('change', () => {
  perform('Carregar projeto', async () => {
    projectId = $('#project-select').value;
    await loadProject();
  }).then(() => fetchFromRemote(true));
});

perform('Carregar painel', async () => {
  projectId = new URLSearchParams(window.location.search).get('projectId');
  await refreshProjects();
  await loadProject();
}).then(() => fetchFromRemote(true));

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) fetchFromRemote();
});
