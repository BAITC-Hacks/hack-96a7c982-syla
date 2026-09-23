let currentRunId = null;
let historyRun = null;
let historyRequest = 0;
const dateTime = new Intl.DateTimeFormat('ru-RU', {dateStyle: 'short', timeStyle: 'short'});

async function saveRun(plan) {
  currentRunId = null;
  byId('open-current-run').hidden = true;
  byId('journal-status').textContent = 'Сохраняем результаты пилотов…';
  try {
    const saved = await responseJson(await fetch('/api/runs', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({seed: plan.seed, dataset: plan.dataset.id}),
    }));
    currentRunId = saved.id;
    byId('journal-status').textContent = saved.new_record
      ? 'Результаты пилотов сохранены в локальном журнале.'
      : 'Этот сценарий уже есть в журнале. Повторная запись не создана.';
    byId('open-current-run').hidden = false;
  } catch (error) {
    byId('journal-status').textContent = `План готов, но журнал не сохранён: ${error.message}`;
  }
}

function renderPilotHistory() {
  const pilots = historyRun.pilots;
  const count = pilots.length;
  const mae = count ? pilots.reduce((sum, pilot) => sum + Math.abs(pilot.error_pp), 0) / count : null;
  const meanBefore = count ? pilots.reduce((sum, pilot) => sum + pilot.std_before_pct, 0) / count : null;
  const meanAfter = count ? pilots.reduce((sum, pilot) => sum + pilot.std_after_pct, 0) / count : null;
  byId('history-metrics').innerHTML = `
    <div><span>Сохранено пилотов</span><strong>${number(count)} <small>/ ${number(pilots.filter(pilot => pilot.round > 1).length)} повторных</small></strong><p>Все ${number(count)} — смоделированные наблюдения</p></div>
    <div><span>Среднее абсолютное отклонение</span><strong>${mae === null ? '—' : decimal.format(mae)} <small>п.п.</small></strong><p>Прогноз до пилота против его наблюдения</p></div>
    <div><span>Средняя σ модели: до → после</span><strong>${count ? `${percent(meanBefore)} → ${percent(meanAfter)}` : '—'}</strong><p>Неопределённость оценок, не точность на тесте</p></div>`;
  const filter = byId('pilot-filter').value;
  const shown = pilots.filter(pilot => filter === 'all' || (filter === 'followup' ? pilot.round > 1 : pilot.decision === 'rejected'));
  const decisions = {selected: 'Включена в план', rejected: 'Отклонена', not_selected: 'Не вошла в план'};
  byId('pilot-history-rows').innerHTML = shown.length ? shown.map(pilot => `
    <tr>
      <th scope="row"><small>#${String(pilot.sequence).padStart(2, '0')} · ${pilot.round > 1 ? `повтор ${pilot.round}` : 'первый пилот'} · ${escapeHtml(channelName(pilot.channel))}</small><strong>${tariffLabel(pilot.source, historyRun.source === 'demo')} → ${tariffLabel(pilot.target, historyRun.source === 'demo')}</strong><span>ARPU ${escapeHtml(pilot.segment)}</span></th>
      <td><strong>${percent(pilot.before_pct)}</strong><small>σ ${percent(pilot.std_before_pct)}</small></td>
      <td class="observed-cell"><strong>${percent(pilot.observed_pct)}</strong><small>SE ${percent(pilot.observation_se_pct)}</small></td>
      <td><strong>${percent(pilot.after_pct)}</strong><small>σ ${percent(pilot.std_after_pct)}</small></td>
      <td><strong>${pilot.error_pp >= 0 ? '+' : ''}${decimal.format(pilot.error_pp)}</strong><small>п.п.</small></td>
      <td><strong>N = ${number(pilot.n)}</strong><small>${number(pilot.cost)} у.е.</small></td>
      <td><span class="decision-label decision-${pilot.decision}">${decisions[pilot.decision]}</span></td>
    </tr>`).join('') : '<tr><td colspan="7" class="history-empty">Пилотов по этому фильтру нет.</td></tr>';
}

async function openHistoryRun(id, request) {
  const run = await responseJson(await fetch(`/api/runs?id=${encodeURIComponent(id)}`));
  if (request !== historyRequest) return;
  historyRun = run;
  byId('run-meta').textContent = `${dateTime.format(new Date(run.created_at))} · ${run.source === 'demo' ? 'Данные кейса · названия тарифов условные, ID в подсказках' : 'Загруженная база'} · ${number(run.customers)} абонентов · сценарий ${run.seed} · версия агента ${run.model_version}`;
  byId('export-pilots').href = `/api/pilots.csv?id=${encodeURIComponent(run.id)}`;
  byId('pilot-filter').value = 'all';
  renderPilotHistory();
  byId('history-content').hidden = false;
  byId('history-message').hidden = true;
}

async function loadHistory(preferredId = currentRunId) {
  const request = ++historyRequest;
  byId('history-message').hidden = false;
  byId('history-message').textContent = 'Загружаем журнал…';
  byId('history-content').hidden = true;
  try {
    const listing = await responseJson(await fetch('/api/runs'));
    if (request !== historyRequest) return;
    if (!listing.runs.length) {
      byId('history-message').textContent = 'Журнал пока пуст. Постройте план на данных кейса или своей базе — результаты пилотов сохранятся здесь автоматически.';
      return;
    }
    byId('run-select').innerHTML = listing.runs.map(run => `<option value="${run.id}">${escapeHtml(dateTime.format(new Date(run.created_at)))} · ${run.source === 'demo' ? 'кейс' : 'своя база'} · сценарий ${run.seed} · ${number(run.customers)} аб.</option>`).join('');
    const id = listing.runs.some(run => run.id === preferredId) ? preferredId : listing.runs[0].id;
    byId('run-select').value = id;
    await openHistoryRun(id, request);
  } catch (error) {
    if (request !== historyRequest) return;
    byId('history-message').textContent = `Не удалось загрузить журнал: ${error.message}`;
  }
}

byId('history-tab').addEventListener('click', () => { showView('history'); loadHistory(); });
byId('open-current-run').addEventListener('click', () => { showView('history'); loadHistory(); });
byId('refresh-history').addEventListener('click', () => loadHistory(historyRun?.id));
byId('pilot-filter').addEventListener('change', renderPilotHistory);
byId('run-select').addEventListener('change', async event => {
  const request = ++historyRequest;
  byId('history-content').hidden = true;
  byId('history-message').hidden = false;
  byId('history-message').textContent = 'Загружаем расчёт…';
  try { await openHistoryRun(event.target.value, request); }
  catch (error) { if (request === historyRequest) byId('history-message').textContent = error.message; }
});
