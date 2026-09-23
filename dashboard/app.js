const byId = (id) => document.getElementById(id);
const format = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
const decimal = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
let report = null;
let activeIndex = 0;
let schemas = {};
let validatedDataset = null;
let loadingPlan = false;

function number(value) { return format.format(Math.round(value)); }
function signed(value) { return `${value >= 0 ? '+' : '−'}${number(Math.abs(value))}`; }
function percent(value) { return Number.isFinite(value) ? `${decimal.format(value)}%` : 'нет данных'; }
function fileSize(bytes) {
  return bytes < 1024 ? `${bytes} Б` : bytes < 1024 * 1024
    ? `${decimal.format(bytes / 1024)} КБ` : `${decimal.format(bytes / 1024 / 1024)} МБ`;
}
function plural(value, one, few, many) {
  const lastTwo = Math.abs(value) % 100;
  const last = lastTwo % 10;
  return lastTwo >= 11 && lastTwo <= 14 ? many : last === 1 ? one : last >= 2 && last <= 4 ? few : many;
}
function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);
}

function metric(label, used, limit, foot) {
  const ratio = Math.min(100, Math.max(0, used / limit * 100));
  return `<div class="metric">
    <span class="metric-label">${label}</span>
    <span class="metric-value">${number(used)} <small>/ ${number(limit)}</small></span>
    <span class="metric-foot">${foot}</span>
    <div class="progress" role="progressbar" aria-label="${label}" aria-valuenow="${Math.round(ratio)}" aria-valuemin="0" aria-valuemax="100"><span style="width:${ratio}%"></span></div>
  </div>`;
}

function renderOverview() {
  const summary = report.summary;
  const limits = report.limits;
  const uploaded = report.dataset.kind === 'upload';
  byId('mode-label').textContent = uploaded
    ? 'Симуляция на загруженных данных · пилоты смоделированы по истории'
    : 'Демо на данных кейса · пилоты смоделированы, рассылки не отправляются';
  const sourceName = uploaded ? report.dataset.files.find(file => file.kind === 'profile').name : report.dataset.label;
  byId('source-label').textContent = `${sourceName} · ${number(report.dataset.customers)} ${plural(report.dataset.customers, 'абонент', 'абонента', 'абонентов')} · ${number(report.dataset.tariffs)} ${plural(report.dataset.tariffs, 'тариф', 'тарифа', 'тарифов')}`;
  byId('download-button').innerHTML = `Скачать ${uploaded ? 'план CSV' : 'submission.csv'} <span aria-hidden="true">↓</span>`;
  byId('seed-label').textContent = `SEED ${report.seed}`;
  byId('plan-heading').textContent = summary.campaigns
    ? `${summary.campaigns} ${plural(summary.campaigns, 'кампания рекомендована', 'кампании рекомендованы', 'кампаний рекомендовано')}`
    : 'Нет кампаний для запуска';
  byId('metrics').innerHTML = [
    metric('Кампании', summary.campaigns, limits.campaigns, `${summary.tested_hypotheses} ${plural(summary.tested_hypotheses, 'гипотеза проверена', 'гипотезы проверены', 'гипотез проверено')}`),
    metric('Контакты', summary.contacts, limits.contacts, `включая ${summary.pilots} ${plural(summary.pilots, 'пилот', 'пилота', 'пилотов')}`),
    metric('Бюджет', summary.budget_used, limits.budget, 'условные единицы'),
  ].join('');
  byId('outcome').innerHTML = `<span class="outcome-label">ЧИСТЫЙ ЭФФЕКТ / ЛОКАЛЬНЫЙ СЦЕНАРИЙ</span>
    <span class="outcome-value">${signed(summary.net_arpu_gain)} <small>у.е.</small></span>
    <span class="outcome-foot">${uploaded ? 'Модельный результат по вашей истории переходов. Реальный эффект ещё не измерен.' : 'Прирост ARPU после затрат на контакты и дедупликации. Не прогноз скрытого результата.'}</span>`;
}

function renderQuickNav() {
  byId('quick-campaign-list').innerHTML = report.campaigns.map((campaign, index) => `
    <button class="quick-campaign ${index === activeIndex ? 'active' : ''}" type="button" data-index="${index}" aria-pressed="${index === activeIndex}">
      <span>${String(index + 1).padStart(2, '0')}</span> ${escapeHtml(campaign.source)} [${escapeHtml(campaign.segment)}] → ${escapeHtml(campaign.target)}
    </button>`).join('');
  byId('quick-campaign-list').querySelectorAll('button').forEach((button) => {
    button.addEventListener('click', () => { activeIndex = Number(button.dataset.index); renderQuickNav(); renderPortfolio(); renderInspector(); });
  });
}

function renderPortfolio() {
  byId('portfolio-count').textContent = `${report.campaigns.length} / ${report.limits.campaigns}`;
  byId('campaign-list').innerHTML = report.campaigns.map((campaign, index) => `
    <button class="campaign-item ${index === activeIndex ? 'active' : ''}" type="button" data-index="${index}" aria-pressed="${index === activeIndex}">
      <span class="campaign-rank">${String(index + 1).padStart(2, '0')}</span>
      <span class="campaign-main">
        <span class="campaign-route">${escapeHtml(campaign.source)} <b>[${escapeHtml(campaign.segment)}] →</b> ${escapeHtml(campaign.target)}</span>
        <span class="campaign-meta"><span class="channel-pill">${escapeHtml(campaign.channel.toUpperCase())}</span> ${number(campaign.contacts)} ${plural(campaign.contacts, 'контакт', 'контакта', 'контактов')} · ${campaign.pilot_count ? `${campaign.pilot_count} ${plural(campaign.pilot_count, 'пилот', 'пилота', 'пилотов')}` : 'без пилота'}</span>
      </span>
      <span class="campaign-gain">${signed(campaign.expected_net)}</span>
    </button>`).join('');
  byId('campaign-list').querySelectorAll('button').forEach((button) => {
    button.addEventListener('click', () => { activeIndex = Number(button.dataset.index); renderPortfolio(); renderQuickNav(); renderInspector(); });
  });
  byId('rejected-count').textContent = String(report.rejected.length);
  byId('rejected-caption').textContent = report.rejected.length
    ? `${report.rejected.length} ${plural(report.rejected.length, 'гипотеза остановлена', 'гипотезы остановлены', 'гипотез остановлено')} после пилота. SMS-рассылка по этим группам стоила бы до ${number(report.summary.potential_sms_cost_avoided)} у.е. — сценарная оценка, не доказанная экономия.`
    : 'В этом прогоне пилоты не выявили уверенно отрицательных кандидатов.';
  byId('rejected-list').innerHTML = report.rejected.length
    ? report.rejected.map((item) => `<div class="rejected-item">
        <div class="rejected-route">${escapeHtml(item.source)} [${escapeHtml(item.segment)}] → ${escapeHtml(item.target)}</div>
        <div class="rejected-reason">Пилот: ${item.pilots.length ? percent(item.pilots[0].observed_lift_pct) : '—'} · верхняя осторожная оценка ${percent(item.posterior_lift_pct + item.posterior_std_pct)}. Повторный пилот не проводился.</div>
      </div>`).join('')
    : '<div class="rejected-item rejected-reason">Другие гипотезы могли не войти в план из-за лимита кампаний или более слабой осторожной оценки.</div>';
}

function confidencePositions(campaign) {
  const low = Math.min(0, campaign.cautious_net);
  const high = Math.max(1, campaign.expected_net);
  const padding = Math.max(1, (high - low) * .12);
  const start = low - padding;
  const end = high + padding;
  const position = (value) => `${Math.max(0, Math.min(100, (value - start) / (end - start) * 100))}%`;
  return {
    zero: position(0), lower: position(campaign.cautious_net),
    point: position(campaign.expected_net),
    band: `${(campaign.expected_net - campaign.cautious_net) / (end - start) * 100}%`,
  };
}

function renderInspector() {
  const campaign = report.campaigns[activeIndex];
  if (!campaign) { byId('inspector').innerHTML = '<div class="empty-state">В этом сценарии агент не выбрал кампаний. Проверьте размер групп и результаты пилотов: группы меньше 10 человек не рассматриваются.</div>'; return; }
  const positions = confidencePositions(campaign);
  const pilotHtml = campaign.pilots.length
    ? campaign.pilots.map((pilot, index) => `<div class="pilot-row">
        <div><strong>Пилот ${index + 1} · N=${number(pilot.n)}</strong> <span>SMS · ${number(pilot.cost)} у.е.</span></div>
        <div>Наблюдаемый lift <strong>${percent(pilot.observed_lift_pct)}</strong> <span>шум ±${percent(pilot.standard_error_pct)}</span></div>
      </div>`).join('')
    : '<div class="pilot-row">Группа мала для отдельного пилота; оценка опирается на исторический prior и имеет повышенную неопределённость.</div>';
  const pilotObservation = campaign.pilots.length ? percent(campaign.pilots[0].observed_lift_pct) : 'нет отдельного пилота';
  const explanation = campaign.pilot_count
    ? `Пилот показал ${pilotObservation}. После учёта шума агент оценивает эффект в ${percent(campaign.posterior_lift_pct)} и выбирает кампанию, потому что осторожный чистый эффект остаётся ${signed(campaign.cautious_net)} у.е. после затрат на связь.`
    : `Для группы из ${number(campaign.audience)} человек отдельный пилот не проводился. Агент использовал историческую оценку и добавил группу только при положительном осторожном эффекте.`;
  const context = campaign.tariff_context;
  const priceChange = context.target_price - context.current_price;
  byId('inspector').innerHTML = `
    <div class="selection-head">
      <div><p class="eyebrow">ВЫБРАННАЯ КАМПАНИЯ</p>
        <h3 class="selection-route">${escapeHtml(campaign.source)} <span class="arrow">→</span> ${escapeHtml(campaign.target)}</h3>
        <div class="selection-tags"><span>ARPU ${escapeHtml(campaign.segment)}</span><span>${escapeHtml(campaign.channel.toUpperCase())}</span><span>${campaign.pilot_count} ${plural(campaign.pilot_count, 'пилот', 'пилота', 'пилотов')}</span></div>
      </div><span class="selection-number">#${String(campaign.rank).padStart(2, '0')}</span>
    </div>
    ${campaign.pilot_count ? '' : '<div class="review-alert"><strong>Требует ручной проверки</strong><span>Эта малая группа вошла в план без отдельного пилота. Оценка основана на исторических переходах другой аудитории; перед реальной рассылкой нужен контрольный тест.</span></div>'}
    <div class="decision-flow" aria-label="Путь решения">
      <div class="flow-step"><span class="flow-index">01 / ДО ПИЛОТА</span><strong>${percent(campaign.prior_lift_pct)}</strong><small>историческая оценка</small></div>
      <div class="flow-step"><span class="flow-index">02 / НАБЛЮДЕНИЕ</span><strong>${pilotObservation}</strong><small>${campaign.pilot_count ? `${campaign.pilot_count} ${plural(campaign.pilot_count, 'пилот', 'пилота', 'пилотов')} · с шумом` : 'история другой аудитории'}</small></div>
      <div class="flow-step flow-decision"><span class="flow-index">03 / РЕШЕНИЕ</span><strong>${signed(campaign.cautious_net)}</strong><small>чистый эффект, среднее − 1σ</small></div>
    </div>
    <div class="detail-grid">
      <div class="detail-cell"><span class="detail-label">Контактов</span><span class="detail-value">${number(campaign.contacts)}</span><span class="detail-sub">из ${number(campaign.audience)} в сегменте</span></div>
      <div class="detail-cell"><span class="detail-label">Затраты</span><span class="detail-value">${number(campaign.cost)} у.е.</span><span class="detail-sub">${number(campaign.cost_per_contact)} у.е. / контакт</span></div>
      <div class="detail-cell"><span class="detail-label">Оценка lift</span><span class="detail-value">${percent(campaign.posterior_lift_pct)}</span><span class="detail-sub">σ модели ${percent(campaign.posterior_std_pct)}</span></div>
    </div>
    <div class="section-title"><h3>Подходит ли переход клиенту?</h3><span>КОНТЕКСТ, НЕ СКОРИНГ</span></div>
    <div class="context-panel">
      <div class="context-item"><span>Абонплата</span><strong>${number(context.current_price)} → ${number(context.target_price)}</strong><small>${signed(priceChange)} у.е. / мес.</small></div>
      <div class="context-item"><span>Пакет интернета</span><strong>${number(context.current_data_gb)} → ${number(context.target_data_gb)} ГБ</strong><small>по справочнику тарифов</small></div>
      <div class="context-item"><span>Потребление сегмента</span><strong>${percent(context.data_user_pct)}</strong><small>пользуются интернетом · ${percent(context.heavy_user_pct)} в группе HEAVY</small><small>Трафик известен у ${number(context.data_known_count)} из ${number(campaign.audience)}</small></div>
    </div>
    <p class="context-note">Эти признаки помогают менеджеру проверить уместность предложения. Текущий агент использует тариф, ARPU-сегмент и результаты пилотов; трафик пока не участвует в ранжировании.</p>
    <div class="section-title"><h3>Эффект с учётом риска</h3><span>ОЦЕНКА АГЕНТА</span></div>
    <div class="confidence-card">
      <div class="confidence-numbers"><div><small>Осторожно: среднее − 1σ</small><strong class="safe">${signed(campaign.cautious_net)} у.е.</strong></div><div><small>Ожидаемый net</small><strong>${signed(campaign.expected_net)} у.е.</strong></div></div>
      <div class="confidence-track" style="--zero:${positions.zero};--lower:${positions.lower};--point:${positions.point};--band:${positions.band}"><span class="zero"></span><span class="band"></span><span class="point"></span></div>
      <div class="confidence-legend"><span><i class="legend-swatch"></i> осторожно → среднее</span><span><i class="legend-swatch legend-point"></i> ожидаемое</span><span>Пунктир — ноль</span></div>
    </div>
    <p class="footnote">Это модельная оценка при текущих допущениях, а не 95% доверительный интервал. Общий итог сверху рассчитан локальным мок-скорером и учитывает пересечения и пилоты.</p>
    <div class="section-title"><h3>Симуляция пилотов</h3><span>ШУМ ∝ 1 / √N</span></div>
    <div class="pilot-list">${pilotHtml}</div>
    <div class="explanation"><h3>${campaign.pilot_count ? 'Почему запускать' : 'Почему агент включил'}</h3><p>${explanation}</p></div>
    <div class="guardrail-note"><strong>Защита от ложного апсейла</strong><span>Считаем чистый ARPU после стоимости канала, учитываем лимиты и не выбираем две кампании на одну группу. Истинный причинный эффект без контрольной группы здесь не доказан.</span></div>`;
}

function showView(view) {
  for (const name of ['data', 'plan', 'history']) {
    byId(`${name}-view`).hidden = name !== view;
    byId(`${name}-tab`).classList.toggle('active', name === view);
    byId(`${name}-tab`).setAttribute('aria-pressed', String(name === view));
  }
  byId('plan-actions').hidden = view !== 'plan';
}

async function responseJson(response) {
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error || `Сервер вернул ${response.status}`);
    error.issues = data.issues;
    throw error;
  }
  return data;
}

async function loadPlan(seed, datasetId = report?.dataset.id || 'demo') {
  if (loadingPlan) return;
  loadingPlan = true;
  const button = byId('generate-button');
  button.disabled = true;
  byId('download-button').disabled = true;
  byId('demo-button').disabled = true;
  if (byId('build-upload-button')) byId('build-upload-button').disabled = true;
  button.textContent = 'Считаем план…';
  const oldDemoText = byId('demo-button').textContent;
  byId('demo-button').textContent = 'Считаем план…';
  if (byId('build-upload-button')) byId('build-upload-button').textContent = 'Считаем план…';
  byId('message').hidden = true;
  try {
    const response = await fetch(`/api/plan?seed=${encodeURIComponent(seed)}&dataset=${encodeURIComponent(datasetId)}`);
    report = await responseJson(response);
    activeIndex = 0;
    byId('seed-input').value = report.seed;
    renderOverview(); renderQuickNav(); renderPortfolio(); renderInspector();
    byId('plan-tab').disabled = false;
    showView('plan');
    window.scrollTo({top: 0, behavior: 'instant'});
    await saveRun(report);
  } catch (error) {
    byId('message').textContent = `Не удалось построить план: ${error.message}. Проверьте, что локальный сервер запущен.`;
    byId('message').hidden = false;
  } finally {
    button.disabled = false;
    byId('download-button').disabled = !report;
    byId('demo-button').disabled = false;
    byId('demo-button').textContent = oldDemoText;
    if (byId('build-upload-button')) {
      byId('build-upload-button').disabled = false;
      byId('build-upload-button').textContent = 'Построить план →';
    }
    loadingPlan = false;
    button.innerHTML = 'Сгенерировать план <span aria-hidden="true">↗</span>';
  }
}

byId('generate-button').addEventListener('click', () => {
  const seed = Number(byId('seed-input').value);
  if (!Number.isInteger(seed) || seed < 0 || seed > 10000) {
    byId('message').textContent = 'Введите номер сценария от 0 до 10 000.';
    byId('message').hidden = false;
    return;
  }
  loadPlan(seed);
});
byId('download-button').addEventListener('click', async () => {
  if (!report) return;
  try {
    const response = await fetch(`/api/submission.csv?seed=${report.seed}&dataset=${encodeURIComponent(report.dataset.id)}`);
    if (!response.ok) await responseJson(response);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url; link.download = report.dataset.kind === 'upload' ? 'campaign_plan.csv' : 'submission.csv';
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    byId('message').textContent = `CSV не скачался: ${error.message}`;
    byId('message').hidden = false;
  }
});
const descriptions = {
  profile: 'Кто ваши клиенты: текущий тариф, ARPU и потребление.',
  tariffs: 'Что можно предложить: доступные тарифы, цена и пакет интернета.',
  history: 'Что происходило раньше: ARPU до и после смены тарифа.',
};

async function initUpload() {
  try {
    schemas = await responseJson(await fetch('/api/data-schema'));
    byId('upload-cards').innerHTML = Object.entries(schemas).map(([kind, schema], index) => `
      <article class="upload-card" id="card-${kind}">
        <div class="file-card-top"><span class="file-symbol">${['БАЗА', 'ТАРИФЫ', 'ИСТОРИЯ'][index]}</span><a href="/api/template.csv?kind=${kind}" download="${schema.filename}">CSV-шаблон ↓</a></div>
        <h3>${escapeHtml(schema.label)}</h3><p class="file-description">${descriptions[kind]}</p>
        <input class="file-input" id="file-${kind}" type="file" accept=".csv,text/csv" aria-label="${escapeHtml(schema.label)}">
        <label class="file-drop" for="file-${kind}"><span class="upload-arrow" aria-hidden="true">↑</span><strong id="filename-${kind}">Выбрать CSV</strong><small id="filestatus-${kind}">${escapeHtml(schema.filename)}</small></label>
        <details class="column-help"><summary>Обязательные колонки · ${schema.columns.length}</summary><div>${schema.columns.map(column => `<code>${escapeHtml(column)}</code>`).join('')}</div><p>До ${number(schema.max_rows)} строк. Дополнительные колонки можно оставить.</p>${kind === 'profile' ? '<p>predicted_arpu — прогноз ARPU без кампании, DATA_VOLUME — потребление данных в МБ. Сегменты: ARPU LOW/MID/HIGH; данные NON_USER/LITE/HEAVY; звонки LOW/MEDIUM/HIGH.</p>' : kind === 'tariffs' ? '<p>price_tariff — абонплата, Data_in_PKG — пакет в МБ.</p>' : '<p>AVG_ARPU_PREV_3M и AVG_ARPU_NEXT_3M — средний ARPU за 3 месяца до и после перехода.</p>'}</details>
      </article>`).join('');
    Object.keys(schemas).forEach(kind => byId(`file-${kind}`).addEventListener('change', () => {
      validatedDataset = null;
      byId('validation-result').hidden = true;
      const file = byId(`file-${kind}`).files[0];
      byId(`filename-${kind}`).textContent = file?.name || 'Выбрать CSV';
      byId(`filestatus-${kind}`).textContent = file ? `${fileSize(file.size)} · нажмите, чтобы заменить` : schemas[kind].filename;
      byId(`card-${kind}`).classList.toggle('has-file', Boolean(file));
      byId('validate-button').disabled = !Object.keys(schemas).every(key => byId(`file-${key}`).files.length);
    }));
  } catch (error) {
    byId('upload-cards').textContent = `Не удалось загрузить форму: ${error.message}. Обновите страницу после запуска сервера.`;
  }
}

byId('upload-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = byId('validate-button');
  const output = byId('validation-result');
  button.disabled = true;
  button.textContent = 'Проверяем файлы…';
  validatedDataset = null;
  output.hidden = true;
  byId('message').hidden = true;
  const inputs = Object.keys(schemas).map(kind => byId(`file-${kind}`));
  inputs.forEach(input => { input.disabled = true; });
  try {
    const files = Object.keys(schemas).map(kind => ({kind, file: byId(`file-${kind}`).files[0]}));
    if (files.some(({file}) => !file)) throw new Error('Выберите все три CSV-файла.');
    if (files.some(({file}) => file.size > 15 * 1024 * 1024) || files.reduce((sum, {file}) => sum + file.size, 0) > 30 * 1024 * 1024) throw new Error('Допустимо до 15 МБ на файл и до 30 МБ вместе.');
    const payload = {};
    for (const {kind, file} of files) {
      if (!file.name.toLowerCase().endsWith('.csv')) throw new Error(`${schemas[kind].label}: выберите .csv файл.`);
      let text;
      try { text = new TextDecoder('utf-8', {fatal: true}).decode(await file.arrayBuffer()); }
      catch { throw new Error(`${schemas[kind].label}: сохраните CSV в кодировке UTF-8.`); }
      payload[kind] = {name: file.name, text};
    }
    validatedDataset = await responseJson(await fetch('/api/datasets', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)}));
    output.className = 'validation-result validation-ok';
    output.innerHTML = `<div class="validation-head"><div><p class="eyebrow">ПРОВЕРКА ЗАВЕРШЕНА</p><h3>Данные готовы к расчёту</h3></div><span class="validation-check" aria-hidden="true">✓</span></div>
      <div class="dataset-counts"><div><strong>${number(validatedDataset.customers)}</strong><span>абонентов для плана</span></div><div><strong>${number(validatedDataset.tariffs)}</strong><span>тарифов</span></div><div><strong>${number(validatedDataset.transitions)}</strong><span>переходов в истории</span></div></div>
      <ul class="checked-files">${validatedDataset.files.map(file => `<li><span>${escapeHtml(file.name)}</span><small>${number(file.rows)} строк · ${number(file.columns)} колонок</small></li>`).join('')}</ul>
      ${validatedDataset.warnings.length ? `<div class="data-warnings"><strong>Учтено при обработке</strong><ul>${validatedDataset.warnings.map(warning => `<li>${escapeHtml(warning)}</li>`).join('')}</ul></div>` : ''}
      <div class="validation-footer"><p>Следующий шаг — симуляция пилотов и подбор кампаний.</p><button id="build-upload-button" class="button button-primary" type="button">Построить план →</button></div>`;
    byId('build-upload-button').addEventListener('click', () => loadPlan(42, validatedDataset.id));
  } catch (error) {
    output.className = 'validation-result validation-error';
    output.innerHTML = `<h3>Проверьте файлы</h3><p>${escapeHtml(error.message)}</p>${error.issues ? `<ul>${error.issues.map(issue => `<li><strong>${escapeHtml(schemas[issue.file]?.label || 'Загрузка')}:</strong> ${escapeHtml(issue.message)}</li>`).join('')}</ul>` : ''}`;
  } finally {
    output.hidden = false;
    inputs.forEach(input => { input.disabled = false; });
    button.disabled = false;
    button.innerHTML = 'Проверить файлы <span aria-hidden="true">→</span>';
    output.scrollIntoView({block: 'nearest', behavior: 'instant'});
  }
});

byId('data-tab').addEventListener('click', () => showView('data'));
byId('change-data-button').addEventListener('click', () => showView('data'));
byId('plan-tab').addEventListener('click', () => showView('plan'));
byId('demo-button').addEventListener('click', () => loadPlan(42, 'demo'));
initUpload();
