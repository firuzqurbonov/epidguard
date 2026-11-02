'use strict';
// All scripts are served locally. Medical form content is never stored in localStorage.
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
function toast(message) { const el = $('#toast'); if (!el) return; el.textContent = message; el.hidden = false; setTimeout(() => el.hidden = true, 7000); }
$$('[data-auto-submit]').forEach(el => el.addEventListener('change', () => el.form.requestSubmit()));
$$('[data-print]').forEach(el => el.addEventListener('click', () => window.print()));
$('.mobile-menu')?.addEventListener('click', event => { const open = $('#sidebar').classList.toggle('open'); event.currentTarget.setAttribute('aria-expanded', String(open)); });
document.addEventListener('click', event => { if (window.innerWidth <= 760 && !event.target.closest('#sidebar,.mobile-menu')) { $('#sidebar')?.classList.remove('open'); $('.mobile-menu')?.setAttribute('aria-expanded', 'false'); } });
document.addEventListener('keydown', event => { if (event.key === 'Escape') { $('#sidebar')?.classList.remove('open'); $('.mobile-menu')?.setAttribute('aria-expanded', 'false'); } });
$$('[data-confirm-form]').forEach(form => form.addEventListener('submit', event => { if (!window.confirm('Выполнить действие? Основание и результат будут сохранены в истории.')) event.preventDefault(); }));
$$('[data-dirty-form]').forEach(form => {
    let dirty = false, submitting = false;
    form.addEventListener('input', () => { dirty = true; const state = $('#save-state'); if (state) state.textContent = 'Есть несохранённые изменения'; });
    form.addEventListener('submit', () => { submitting = true; });
    window.addEventListener('beforeunload', event => { if (dirty && !submitting) { event.preventDefault(); event.returnValue = ''; } });
});
function applyConditions() {
    $$('[data-depends]').forEach(field => {
        const source = document.getElementsByName(field.dataset.depends)[0];
        const shown = source?.value === field.dataset.value;
        const container = field.closest('.form-field');
        if (container) container.hidden = !shown;
        field.disabled = !shown;
    });
}
document.addEventListener('change', applyConditions);
applyConditions();
const diseaseSelect = $('#case-form #id_disease');
let questionnaireRequest;
if (diseaseSelect) diseaseSelect.addEventListener('change', async () => {
    const container = $('#questionnaire-fields');
    questionnaireRequest?.abort();
    if (!diseaseSelect.value) { container.replaceChildren(); $('#questionnaire-version').textContent = 'Выберите заболевание'; return; }
    const controller = new AbortController(); questionnaireRequest = controller;
    try {
        const caseId = $('#case-form').dataset.caseId;
        const response = await fetch(`/api/diseases/${encodeURIComponent(diseaseSelect.value)}/questionnaire/${caseId ? '?case=' + encodeURIComponent(caseId) : ''}`, { signal: controller.signal, headers: {'Accept': 'application/json'} });
        if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('questionnaire');
        const data = await response.json();
        const fragment = document.createDocumentFragment();
        data.questions.forEach(q => {
            const wrapper = document.createElement('div'); wrapper.className = 'form-field';
            const label = document.createElement('label'); label.htmlFor = `id_q_${q.key}`;
            label.textContent = (q[`label_${data.language}`] || q.label_ru) + (q.required ? ' *' : '');
            let field;
            if (q.type === 'select') {
                field = document.createElement('select');
                field.add(new Option('—', ''));
                (q.options || []).forEach(value => field.add(new Option(value, value)));
            } else if (q.type === 'date') { field = document.createElement('input'); field.type = 'date'; }
            else { field = document.createElement('textarea'); field.rows = 2; field.maxLength = 3000; }
            field.name = `q_${q.key}`; field.id = `id_q_${q.key}`;
            if (q.show_if) { field.dataset.depends = `q_${q.show_if.key}`; field.dataset.value = q.show_if.value; }
            wrapper.append(label, field); fragment.append(wrapper);
        });
        container.replaceChildren(fragment);
        $('#questionnaire-version').textContent = data.version ? `v${data.version} · ${data.approved ? 'Утверждена' : 'Проект'}` : 'Анкета не настроена';
        applyConditions();
    } catch (error) {
        if (error.name !== 'AbortError') { container.replaceChildren(); $('#questionnaire-version').textContent = 'Не удалось загрузить анкету'; toast('Проверьте соединение и выберите болезнь повторно. Сервер проверит анкету при сохранении.'); }
    }
});
// Inline SVG uses DOM APIs and textContent, never unsanitized HTML.
function svgElement(name, attrs = {}, text) { const el = document.createElementNS('http://www.w3.org/2000/svg', name); Object.entries(attrs).forEach(([k,v]) => el.setAttribute(k, v)); if (text !== undefined) el.textContent = text; return el; }
const curve = $('#epicurve');
if (curve && $('#chart-data')) {
    const data = JSON.parse($('#chart-data').textContent);
    const width = 620, height = 192, left = 33, bottom = 27, top = 16;
    const svg = svgElement('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': 'Число случаев по дате выявления за 14 дней' });
    const max = Math.max(4, ...data.map(row => row.value)), ceiling = Math.ceil(max / 4) * 4;
    for (let i = 0; i <= 4; i++) {
        const y = height - bottom - (height - top - bottom) * i / 4;
        svg.append(svgElement('line', { x1: left, y1: y, x2: width - 8, y2: y, class: 'chart-grid' }), svgElement('text', {x: left - 10, y: y + 3, 'text-anchor': 'end'}, ceiling * i / 4));
    }
    const column = (width - left - 12) / data.length;
    data.forEach((row, i) => {
        const barHeight = (height - top - bottom) * row.value / ceiling;
        const bar = svgElement('rect', { x: left + i * column + 9, y: height - bottom - barHeight, width: Math.max(5, column - 18), height: barHeight, class: 'bar', tabindex: '0' });
        bar.append(svgElement('title', {}, `${row.label}: ${row.value}`)); svg.append(bar);
        if (i % 2 === 0 || i === data.length - 1) svg.append(svgElement('text', {x: left + (i + .5) * column, y: height - 6, 'text-anchor': 'middle'}, row.label));
    });
    curve.append(svg);
}
const map = $('#territory-map');
if (map && $('#map-data')) {
    const data = JSON.parse($('#map-data').textContent);
    const svg = svgElement('svg', { viewBox: '0 0 780 480', role: 'img', 'aria-label': 'Агрегированные случаи по центрам территорий' });
    const minLon = 67.7, maxLon = 74, minLat = 36.8, maxLat = 41.1;
    function project(lon, lat) {return [55 + (lon - minLon) / (maxLon - minLon) * 665, 430 - (lat - minLat) / (maxLat - minLat) * 380];}
    svg.append(svgElement('text', { x: 28, y: 33, fill: '#7e9a91', 'font-size': '10', 'letter-spacing': '3' }, 'TAJIKISTAN · TERRITORY CENTRES'));
    const maxCount = Math.max(1, ...data.map(row => row.count));
    // Nearby districts are spread deterministically on the diagram to keep labels readable.
    const placed = [];
    data.forEach(row => {
        const [baseX, baseY] = project(row.lon, row.lat);
        let x = baseX, y = baseY, attempt = 0;
        while (placed.some(p => Math.hypot(p[0] - x, p[1] - y) < 80) && attempt < 18) { attempt++; x = baseX + Math.cos(attempt * 2.4) * (45 + attempt * 5); y = baseY + Math.sin(attempt * 2.4) * (45 + attempt * 5); }
        x = Math.max(60, Math.min(710, x)); y = Math.max(65, Math.min(400, y)); placed.push([x,y]);
        if (attempt) svg.append(svgElement('line', {x1: baseX, y1: baseY, x2:x, y2:y, stroke:'#8bb2a5', 'stroke-dasharray':'3 4'}));
        const radius = 13 + Math.sqrt(row.count / maxCount) * 16;
        const link = svgElement('a', { href: `/cases/?territory=${row.id}` });
        link.append(svgElement('circle', { cx:x, cy:y, r:radius + 9, fill:'#14866f', opacity:'.07' }), svgElement('circle', {cx:x, cy:y, r:radius, fill:'#1a947b', opacity:'.9'}), svgElement('text', {x, y:y + 4, 'text-anchor':'middle', class:'map-count'}, row.count));
        link.append(svgElement('title', {}, `${row.name}: ${row.count}. Открыть реестр территории.`));
        svg.append(link, svgElement('text', {x, y:y + radius + 23, 'text-anchor':'middle', class:'map-location-label'}, row.name));
    });
    if (!data.length) svg.append(svgElement('text', { x:390, y:240, 'text-anchor':'middle', fill:'#708f86', 'font-size':'15'}, 'Нет геопривязанных случаев'));
    svg.append(svgElement('text', {x:28, y:460, fill:'#7e9a91', 'font-size':'10'}, 'Схематичное размещение · близкие точки разнесены для читаемости · без государственных границ'));
    map.append(svg);
}
