// Clinic dashboard: cases, plan editor with the 3D simulator, delivery and settings.

import { h, add, clear, put, formatMoney } from '../shared/dom.js';
import { mountSimulator } from '../sim/simulator.js';

const root = document.getElementById('app');
const store = { settings: null, status: null, currencies: null, simRanges: null, toothRanges: null, library: null };
let leave = []; // teardown callbacks for the current view

const STATUS = { draft: 'Draft', approved: 'Approved', sent: 'Sent', viewed: 'Viewed', booked: 'Booked', declined: 'Declined', expired: 'No response' };
const LANGS = { hy: 'Armenian', ru: 'Russian', en: 'English' };
const CHANNELS = { whatsapp: 'WhatsApp', telegram: 'Telegram' };
const APPLIANCES = { metal: 'Metal braces', ceramic: 'Ceramic braces', aligners: 'Clear aligners' };
const REASONS = { price: 'Price', timing: 'Timing', other_clinic: 'Chose another clinic', not_interested: 'Not interested', other: 'Other', opt_out: 'Asked not to be contacted' };
const EVENTS = {
  created: 'Case created', drafted: 'Plan drafted', approved: 'Plan approved', sent: 'Plan sent', viewed: 'Patient opened the plan',
  sim_played: 'Patient played the 3D preview', option_selected: 'Patient looked at an option', compare_used: 'Patient compared before and after',
  followup_sent: 'Follow-up sent', followups_paused: 'Follow-ups paused', followups_resumed: 'Follow-ups resumed',
  followups_stopped: 'Follow-ups stopped after repeated send failures', question: 'Patient asked a question', replied: 'Clinic replied',
  booked: 'Patient asked to start', declined: 'Patient declined', expired: 'Closed: no response', telegram_linked: 'Patient connected on Telegram',
  awaiting_telegram: 'Waiting for the patient to open the Telegram link', manual_booked: 'Marked as booked', manual_declined: 'Marked as declined', manual_reopen: 'Case reopened',
};
const SIM_FIELDS = [
  ['crowding_upper', 'Upper crowding'], ['crowding_lower', 'Lower crowding'], ['spacing_upper', 'Upper spacing'], ['spacing_lower', 'Lower spacing'],
  ['diastema', 'Midline gap'], ['midline', 'Midline shift'], ['overjet', 'Overjet (normal 2.5)'], ['overbite', 'Overbite (normal 2)'], ['narrow_upper', 'Upper arch narrowing'],
];
const TOOTH_FIELDS = [['rot', 'Rotation', '°'], ['inout', 'In / out', 'mm'], ['vert', 'Extrusion', 'mm'], ['tip', 'Tip', '°']];

// ---------------------------------------------------------------- helpers

async function api(method, url, body) {
  const options = { method, headers: { 'X-Requested-With': 'orthocase' }, credentials: 'same-origin' };
  if (body instanceof FormData) options.body = body;
  else if (body !== undefined) { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
  const response = await fetch(url, options);
  if (response.status === 401 && url !== '/api/login') { showLogin(); throw new Error('Signed out'); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

function toast(message, kind = '') {
  const node = h(`div.toast${kind ? `.${kind}` : ''}`, message);
  document.getElementById('toasts').append(node);
  setTimeout(() => node.remove(), kind === 'error' ? 6000 : 3200);
}

const guard = (fn) => async (...args) => { try { return await fn(...args); } catch (error) { if (error.message !== 'Signed out') toast(error.message, 'error'); } };
const money = (value) => (value || value === 0 ? formatMoney(value, store.settings.currency, store.currencies[store.settings.currency].symbol) : '—');
const when = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—');

function field(label, control) { return h('label.field', label, control); }
function input(type, value, oninput, extra = {}) {
  return h('input', { type, value: value ?? '', oninput: (event) => oninput(type === 'number' ? Number(event.target.value) : event.target.value), ...extra });
}
function select(value, options, onchange) {
  return h('select', { onchange: (event) => onchange(event.target.value) },
    Object.entries(options).map(([key, label]) => h('option', { value: key, selected: key === value }, label)));
}

function teardown() { for (const fn of leave) fn(); leave = []; }

// ---------------------------------------------------------------- shell

function showLogin() {
  teardown();
  const error = h('p.error-text', { role: 'alert' });
  const password = h('input', { type: 'password', autocomplete: 'current-password', required: true });
  const form = h('form', {
    onsubmit: async (event) => {
      event.preventDefault();
      try { await api('POST', '/api/login', { password: password.value }); await boot(); } catch (err) { error.textContent = err.message; }
    },
  }, field('Clinic password', password), error, h('button.btn.primary', { type: 'submit' }, 'Sign in'));
  put(root, h('div.login', h('div.card', h('h1', 'OrthoCase'), h('p.muted', 'Treatment plans patients say yes to.'), form)));
  password.focus();
}

function shell(active) {
  const s = store.status;
  const pill = (label, live, text) => h(`span.pill${live ? '.live' : ''}`, `${label}: ${text}`);
  const view = h('main');
  put(root, 
    h('header.top',
      h('div.brand', 'Ortho', h('span', 'Case')),
      h('nav', h(`a${active === 'cases' ? '.is-on' : ''}`, { href: '#/' }, 'Cases'), h(`a${active === 'settings' ? '.is-on' : ''}`, { href: '#/settings' }, 'Settings')),
      h('div.spacer'),
      h('div.pills',
        pill('AI drafting', s.ai.enabled, s.ai.enabled ? 'on' : 'off'),
        pill('WhatsApp', s.whatsapp.mode === 'live', s.whatsapp.mode),
        pill('Telegram', s.telegram.mode === 'live', s.telegram.mode)),
      h('button.link-btn', { type: 'button', onclick: guard(async () => { await api('POST', '/api/logout'); showLogin(); }) }, 'Sign out'),
    ),
    view,
  );
  return view;
}

async function loadSettings() {
  const data = await api('GET', '/api/settings');
  Object.assign(store, { settings: data.settings, status: data.status, currencies: data.currencies, simRanges: data.sim_ranges, toothRanges: data.tooth_ranges, library: data.findings_library });
}

// ---------------------------------------------------------------- cases list

async function viewCases() {
  const view = shell('cases');
  let filter = 'all';
  let data = await api('GET', '/api/cases');
  const body = h('div');

  const draw = () => {
    const st = data.stats;
    const rows = data.cases.filter((c) => filter === 'all' || (filter === 'reply' ? c.pending_question : c.status === filter));
    const count = (key) => data.cases.filter((c) => (key === 'reply' ? c.pending_question : c.status === key)).length;
    put(body, 
      h('div.stats',
        h('div.card.stat', h('div.label', 'Plans sent'), h('div.value', st.sent), h('div.sub', `${st.total} case${st.total === 1 ? '' : 's'} in total`)),
        h('div.card.stat', h('div.label', 'Opened by patient'), h('div.value', st.view_rate === null ? '—' : `${st.view_rate}%`), h('div.sub', `${st.viewed} of ${st.sent}`)),
        h('div.card.stat', h('div.label', 'Accepted'), h('div.value', st.accept_rate === null ? '—' : `${st.accept_rate}%`), h('div.sub', `${st.booked} booked · ${st.declined} declined`)),
        h('div.card.stat', h('div.label', 'Booked value'), h('div.value', money(st.booked_value)), h('div.sub', 'from accepted plans')),
        h('div.card.stat', h('div.label', 'Awaiting decision'), h('div.value', money(st.open_value)), h('div.sub', st.needs_reply ? `${st.needs_reply} waiting for your reply` : 'in sent plans')),
      ),
      h('div.filters', [['all', 'All'], ['reply', 'Needs reply'], ['draft', 'Draft'], ['approved', 'Approved'], ['sent', 'Sent'], ['viewed', 'Viewed'], ['booked', 'Booked'], ['declined', 'Declined'], ['expired', 'No response']]
        .map(([key, label]) => h(`button${filter === key ? '.is-on' : ''}`, { type: 'button', onclick: () => { filter = key; draw(); } },
          key === 'all' ? `${label} ${data.cases.length}` : `${label} ${count(key)}`))),
      h('div.card.table-wrap',
        rows.length === 0
          ? h('div.empty-state', data.cases.length === 0 ? 'No cases yet. Create one to draft your first treatment plan.' : 'No cases match this filter.')
          : h('table',
            h('thead', h('tr', h('th', 'Patient'), h('th', 'Status'), h('th', 'Channel'), h('th', 'Opened'), h('th', 'Next follow-up'), h('th.num', 'Value'), h('th', 'Updated'))),
            h('tbody', rows.map((c) => h('tr', { onclick: () => { location.hash = `#/case/${c.id}`; } },
              h('td.patient-cell', c.patient_name),
              h('td', h('div.row', h(`span.chip.${c.status}`, STATUS[c.status]), c.pending_question && h('span.chip.flag', 'Needs reply'), c.send_pending && h('span.chip.flag', 'Waiting for Telegram'))),
              h('td', CHANNELS[c.channel]),
              h('td', c.view_count ? `${c.view_count}×` : '—'),
              h('td', ['sent', 'viewed'].includes(c.status) ? (c.followups_paused ? 'Paused' : c.pending_question ? 'On hold' : when(c.next_followup_at)) : '—'),
              h('td.num', money(c.value)),
              h('td.muted', when(c.updated_at)),
            ))),
          ),
      ),
    );
  };

  add(view, h('div.page-head', h('h1', 'Cases'), h('button.btn.primary', { type: 'button', onclick: newCaseDialog }, 'New case')), body);
  draw();
  const timer = setInterval(guard(async () => { data = await api('GET', '/api/cases'); draw(); }), 20000);
  leave.push(() => clearInterval(timer));
}

function newCaseDialog() {
  const form = { patient_name: '', phone: '', channel: 'whatsapp', language: store.settings.default_language };
  const name = input('text', '', (v) => { form.patient_name = v; }, { required: true, placeholder: 'Full name' });
  const dialog = h('dialog',
    h('form', {
      onsubmit: guard(async (event) => {
        event.preventDefault();
        const created = await api('POST', '/api/cases', form);
        dialog.close();
        location.hash = `#/case/${created.id}`;
      }),
    },
    h('h2', 'New case'),
    h('div.stack',
      field('Patient name', name),
      field('Phone (with country code)', input('tel', '', (v) => { form.phone = v; }, { placeholder: '+374 91 123456' })),
      h('div.grid2',
        field('Send via', select(form.channel, CHANNELS, (v) => { form.channel = v; })),
        field('Patient language', select(form.language, LANGS, (v) => { form.language = v; }))),
    ),
    h('div.actions', h('button.btn', { type: 'button', onclick: () => dialog.close() }, 'Cancel'), h('button.btn.primary', { type: 'submit' }, 'Create case'))),
  );
  dialog.addEventListener('close', () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  name.focus();
}

// ---------------------------------------------------------------- case editor

async function viewCase(id) {
  const view = shell('cases');
  const ctx = { c: await api('GET', `/api/cases/${id}`), tab: 'plan', dirty: false, timer: null, sim: null, optionId: null, tooth: null };
  const pick = ctx.c.plan.options.find((o) => o.recommended) || ctx.c.plan.options[0];
  ctx.optionId = pick ? pick.id : null;

  const saveState = h('span.save-state');
  const headActions = h('div.row');
  const title = h('h1', ctx.c.patient_name);
  const statusChip = h('span');
  const tabs = h('div.tabs');
  const body = h('div');

  // -- saving: edits are kept locally and written back a moment after the last change
  const save = async () => {
    clearTimeout(ctx.timer);
    if (!ctx.dirty) return;
    ctx.dirty = false;
    saveState.textContent = 'Saving…';
    const c = ctx.c;
    try {
      const fresh = await api('PATCH', `/api/cases/${id}`, { patient_name: c.patient_name, phone: c.phone, channel: c.channel, language: c.language, notes: c.notes, plan: c.plan });
      Object.assign(c, { pricing: fresh.pricing, updated_at: fresh.updated_at, channel_mode: fresh.channel_mode, telegram_link: fresh.telegram_link });
      saveState.textContent = ctx.dirty ? 'Unsaved' : 'Saved';
      for (const node of body.querySelectorAll('[data-pricing]')) node.textContent = pricingText(c.pricing[node.dataset.pricing]);
    } catch (error) {
      ctx.dirty = true;
      saveState.textContent = 'Not saved';
      throw error;
    }
  };
  const touch = () => { ctx.dirty = true; saveState.textContent = 'Unsaved'; clearTimeout(ctx.timer); ctx.timer = setTimeout(guard(save), 900); };
  const refresh = async (fresh) => { ctx.c = fresh || await api('GET', `/api/cases/${id}`); drawHead(); drawTab(); };
  const act = (fn) => guard(async () => { await save(); await fn(); });

  const pricingText = (p) => {
    if (!p) return '';
    const parts = [];
    if (p.monthly) parts.push(`${p.down ? `${money(p.down)} to start, then ` : ''}${p.months} × ${money(p.monthly)} per month`);
    if (p.savings) parts.push(`${money(p.full_price)} if paid in full`);
    return parts.length ? `Patient sees: ${money(p.total)} · ${parts.join(' · or ')}` : `Patient sees: ${money(p.total)}`;
  };

  // -- header
  function drawHead() {
    const c = ctx.c;
    title.textContent = c.patient_name;
    statusChip.className = `chip ${c.status}`;
    statusChip.textContent = STATUS[c.status];
    const via = `${CHANNELS[c.channel]}${c.channel_mode === 'simulated' ? ' (simulated)' : ''}`;
    put(headActions, 
      h('a.btn', { href: `${new URL(c.plan_link).pathname}?preview=1`, target: '_blank', rel: 'noopener', onclick: () => { save().catch(() => {}); } }, 'Preview as patient'),
      c.status === 'draft' && h('button.btn.primary', { type: 'button', onclick: act(async () => { await refresh(await api('POST', `/api/cases/${id}/approve`)); toast('Plan approved. It can now be sent.'); }) }, 'Approve plan'),
      c.status === 'approved' && h('button.btn.primary', { type: 'button', onclick: act(sendPlan) }, c.send_pending ? 'Waiting for Telegram…' : `Send via ${via}`),
      ['sent', 'viewed'].includes(c.status) && h('button.btn', { type: 'button', onclick: act(sendPlan) }, 'Resend plan'),
    );
    put(tabs, 
      h(`button${ctx.tab === 'plan' ? '.is-on' : ''}`, { type: 'button', onclick: () => { ctx.tab = 'plan'; drawHead(); drawTab(); } }, 'Plan'),
      h(`button${ctx.tab === 'delivery' ? '.is-on' : ''}`, { type: 'button', onclick: guard(async () => { await save(); ctx.tab = 'delivery'; await refresh(); }) },
        'Delivery & follow-up', c.pending_question && h('span.dot', { title: 'Patient is waiting for a reply' })),
    );
  }

  async function sendPlan() {
    const { result, case: fresh } = await api('POST', `/api/cases/${id}/send`);
    ctx.tab = 'delivery';
    await refresh(fresh);
    if (!result.ok) toast(result.error, 'error');
    else if (result.pending) toast('The plan will go out as soon as the patient opens the Telegram link.');
    else toast(result.mode === 'simulated' ? 'Recorded as sent (simulated mode — nothing left this computer).' : 'Plan sent.');
  }

  // -- plan tab ------------------------------------------------------------

  function patientCard() {
    const c = ctx.c;
    return h('section.card', h('h2', 'Patient'), h('div.stack',
      field('Name', input('text', c.patient_name, (v) => { c.patient_name = v; title.textContent = v; touch(); })),
      field('Phone', input('tel', c.phone, (v) => { c.phone = v; touch(); }, { placeholder: '+374 91 123456' })),
      h('div.grid2',
        field('Send via', select(c.channel, CHANNELS, (v) => { c.channel = v; touch(); guard(save)().then(drawHead); })),
        field('Language', select(c.language, LANGS, (v) => { c.language = v; touch(); }))),
    ));
  }

  function notesCard() {
    const c = ctx.c;
    const ai = store.status.ai.enabled;
    const button = h('button.btn.primary', { type: 'button' }, ai ? 'Draft plan with AI' : 'Draft plan from notes');
    button.addEventListener('click', act(async () => {
      const hasText = c.plan.summary || c.plan.findings.length;
      if (hasText && !window.confirm('Replace the current summary, findings, simulator values and options with a new draft?')) return;
      button.disabled = true;
      button.textContent = 'Drafting…';
      try { await refresh(await api('POST', `/api/cases/${id}/draft`)); toast('Draft ready — review it before approving.'); }
      finally { button.disabled = false; button.textContent = ai ? 'Draft plan with AI' : 'Draft plan from notes'; }
    }));
    const meta = c.draft_meta;
    return h('section.card', h('h2', 'Clinical notes'), h('div.stack',
      h('textarea', { rows: 7, placeholder: 'e.g. Upper crowding 6 mm, lower 4 mm. Deep bite. Overjet 5 mm. Patient prefers something discreet.', value: c.notes, oninput: (e) => { c.notes = e.target.value; touch(); } }),
      h('div.row', button, h('span.muted.small', ai ? 'Uses your notes and the uploaded images.' : 'Reads keywords and measurements from the notes.')),
      meta && meta.notice && h('div.notice', meta.notice),
      meta && meta.checks && meta.checks.length > 0 && h('div.notice', h('strong', 'Check before sending'), h('ul', meta.checks.map((item) => h('li', item)))),
    ));
  }

  function recordsCard() {
    const c = ctx.c;
    const list = h('div.records');
    const drawList = () => {
      put(list, c.attachments.length === 0
        ? h('p.muted.small', 'No images yet. Panoramic X-rays and intraoral photos as JPEG or PNG.')
        : c.attachments.map((a) => {
          const patch = guard(async (changes) => { Object.assign(a, await api('PATCH', `/api/attachments/${a.id}`, changes)); });
          return h('div.record',
            h('a', { href: a.url, target: '_blank', rel: 'noopener' }, h('img', { src: a.url, alt: a.filename, loading: 'lazy' })),
            h('div.controls',
              h('div.line',
                select(a.kind, { xray: 'X-ray', photo: 'Photo' }, (v) => patch({ kind: v })),
                h('label.check', h('input', { type: 'checkbox', checked: a.shared, onchange: (e) => patch({ shared: e.target.checked }) }), 'Show to patient'),
                h('div', { style: 'flex:1' }),
                h('button.icon-btn', { type: 'button', title: 'Delete image', 'aria-label': 'Delete image', onclick: guard(async () => {
                  if (!window.confirm('Delete this image?')) return;
                  await api('DELETE', `/api/attachments/${a.id}`);
                  c.attachments = c.attachments.filter((x) => x.id !== a.id);
                  drawList();
                }) }, '✕')),
              h('input', { type: 'text', placeholder: 'Caption the patient sees (optional)', value: a.caption, onchange: (e) => patch({ caption: e.target.value }) }),
            ));
        }));
    };
    const upload = (kind) => {
      const picker = h('input', { type: 'file', accept: 'image/*', multiple: true, hidden: true });
      picker.addEventListener('change', guard(async () => {
        for (const file of picker.files) {
          const form = new FormData();
          form.append('file', file);
          try { c.attachments.push(await api('POST', `/api/cases/${id}/attachments?kind=${kind}`, form)); } catch (error) { toast(`${file.name}: ${error.message}`, 'error'); }
          drawList();
        }
      }));
      return h('span', picker, h('button.btn.small', { type: 'button', onclick: () => picker.click() }, kind === 'xray' ? 'Add X-ray' : 'Add photo'));
    };
    drawList();
    return h('section.card', h('div.card-head', h('h2', 'X-rays & photos'), h('div.row', upload('xray'), upload('photo'))), list,
      h('p.muted.small', { style: 'margin-top:10px' }, 'Images stay private unless “Show to patient” is ticked. Location and camera data are removed on upload.'));
  }

  function simulatorCard() {
    const sim = ctx.c.plan.sim;
    const host = h('div');
    const toothPanel = h('div');
    const sliderRow = (label, value, range, step, unit, onInput) => {
      const out = h('output', `${value}${unit}`);
      return h('label.slider', h('span', label), out,
        h('input', { type: 'range', min: range[0], max: range[1], step, value, oninput: (e) => { const v = Number(e.target.value); out.textContent = `${v}${unit}`; onInput(v); } }));
    };
    const push = (toStart) => { if (!ctx.sim || !ctx.sim.sim) return; ctx.sim.sim.setParams(sim); if (toStart) ctx.sim.sim.setProgress(0); };

    const drawTooth = () => {
      clear(toothPanel);
      const fdi = ctx.tooth;
      if (!fdi) return;
      const values = sim.teeth[fdi] || { rot: 0, inout: 0, vert: 0, tip: 0 };
      add(toothPanel, h('div.tooth-panel',
        h('div.card-head', h('h3', `Tooth ${fdi} — fine adjustment of the starting position`),
          h('div.row',
            h('button.btn.small', { type: 'button', onclick: () => { delete sim.teeth[fdi]; push(true); touch(); drawTooth(); } }, 'Reset tooth'),
            h('button.btn.small', { type: 'button', onclick: () => { ctx.tooth = null; ctx.sim.sim.selectTooth(null); drawTooth(); } }, 'Done'))),
        h('div.sliders', TOOTH_FIELDS.map(([key, label, unit]) => sliderRow(label, values[key] || 0, store.toothRanges[key], key === 'inout' || key === 'vert' ? 0.25 : 1, unit,
          (v) => { sim.teeth[fdi] = { ...values, ...(sim.teeth[fdi] || {}), [key]: v }; push(true); touch(); }))),
      ));
    };

    queueMicrotask(() => {
      ctx.sim = mountSimulator(host, { editable: true, onSelectTooth: (fdi) => { ctx.tooth = fdi; drawTooth(); } });
      syncSim();
    });

    return h('section.card',
      h('div.card-head', h('h2', '3D simulation'),
        h('button.btn.small', { type: 'button', onclick: () => {
          for (const [key] of SIM_FIELDS) sim[key] = store.simRanges[key][2];
          sim.teeth = {};
          touch(); drawTab();
        } }, 'Reset to aligned')),
      host,
      h('p.muted.small', { style: 'margin-top:12px' }, 'Starting position. Set the measurements from your examination; click a tooth in the model to fine-tune it. The model is an illustration, not a scan.'),
      h('div.sliders', SIM_FIELDS.map(([key, label]) => sliderRow(label, sim[key], store.simRanges[key], 0.5, ' mm', (v) => { sim[key] = v; push(true); touch(); }))),
      toothPanel,
    );
  }

  function syncSim() {
    if (!ctx.sim || !ctx.sim.sim) return;
    const option = ctx.c.plan.options.find((o) => o.id === ctx.optionId) || ctx.c.plan.options[0];
    ctx.sim.sim.setParams(ctx.c.plan.sim);
    if (option) {
      ctx.sim.sim.setOutcome(option.align_pct, option.bite_pct);
      ctx.sim.sim.setAppliance(option.appliance);
      ctx.sim.setMonths(option.duration_months);
    } else {
      ctx.sim.sim.setOutcome(100, 100);
      ctx.sim.sim.setAppliance('none');
    }
  }

  function textCard() {
    const plan = ctx.c.plan;
    const list = h('div');
    const drawList = () => {
      put(list, plan.findings.length === 0 ? h('p.muted.small', 'No findings yet.') : plan.findings.map((finding, index) => h('div.finding-edit',
        h('input', { type: 'text', placeholder: 'Finding, in plain words', value: finding.title, oninput: (e) => { finding.title = e.target.value; touch(); } }),
        h('button.icon-btn', { type: 'button', title: 'Remove finding', 'aria-label': 'Remove finding', onclick: () => { plan.findings.splice(index, 1); touch(); drawList(); } }, '✕'),
        h('textarea', { placeholder: 'What it means for the patient', value: finding.detail, oninput: (e) => { finding.detail = e.target.value; touch(); } }),
      )));
    };
    drawList();
    const lang = ctx.c.language;
    const library = h('select', { onchange: (e) => {
      const key = e.target.value;
      if (!key) return;
      const [titleText, detail] = store.library[key][lang];
      plan.findings.push({ title: titleText, detail });
      e.target.value = '';
      touch(); drawList();
    } }, h('option', { value: '' }, `Add a common finding (${LANGS[lang]})…`), Object.keys(store.library).map((key) => h('option', { value: key }, store.library[key].en[0])));
    return h('section.card', h('h2', 'What the patient reads'), h('div.stack',
      field('Summary', h('textarea', { rows: 4, placeholder: 'Two or three sentences in the patient’s language.', value: plan.summary, oninput: (e) => { plan.summary = e.target.value; touch(); } })),
      h('div', h('div.card-head', h('h3', 'Findings'), h('div.row', library, h('button.btn.small', { type: 'button', onclick: () => { plan.findings.push({ title: '', detail: '' }); touch(); drawList(); } }, 'Add blank'))), list),
      h('div.grid2', field('Prices valid until', input('date', plan.valid_until, (v) => { plan.valid_until = v; touch(); }))),
    ));
  }

  function optionsCard() {
    const plan = ctx.c.plan;
    const lang = ctx.c.language;
    const list = h('div.options-edit');
    const fromCatalog = (item) => {
      let n = plan.options.length + 1;
      while (plan.options.some((o) => o.id === `opt${n}`)) n += 1;
      return {
        id: `opt${n}`, catalog_id: item.id, name: item.name[lang] || item.name.en || item.id, tagline: item.tagline[lang] || item.tagline.en || '',
        appliance: item.appliance, duration_months: item.months, price: item.price, down_payment: Math.round((item.price * item.down_pct) / 100000) * 1000 || Math.round((item.price * item.down_pct) / 100),
        installment_months: item.installment_months, full_discount_pct: item.full_discount_pct, includes: [...(item.includes[lang] || item.includes.en || [])],
        note: '', recommended: plan.options.length === 0, align_pct: 100, bite_pct: 100,
      };
    };
    const drawList = () => {
      put(list, plan.options.length === 0 ? h('p.muted.small', 'Add at least one option from your catalog.') : plan.options.map((option, index) => {
        const set = (key, numeric) => (v) => { option[key] = numeric ? Number(v) || 0 : v; touch(); if (option.id === ctx.optionId) syncSim(); };
        const num = (key, label, extra = {}) => field(label, input('number', option[key], set(key, true), { min: 0, ...extra }));
        return h(`div.option-edit${option.id === ctx.optionId ? '.is-active' : ''}`,
          h('div.head',
            h('input', { type: 'text', value: option.name, 'aria-label': 'Option name', oninput: (e) => { option.name = e.target.value; touch(); } }),
            h('label.check', h('input', { type: 'radio', name: 'recommended', checked: option.recommended, onchange: () => { plan.options.forEach((o) => { o.recommended = o === option; }); touch(); } }), 'Recommended'),
            h('button.btn.small', { type: 'button', onclick: () => { ctx.optionId = option.id; syncSim(); if (ctx.sim && ctx.sim.sim) { ctx.sim.sim.setProgress(0); ctx.sim.sim.play(); } drawList(); } }, 'Show in 3D'),
            h('button.icon-btn', { type: 'button', title: 'Remove option', 'aria-label': 'Remove option', onclick: () => { plan.options.splice(index, 1); if (plan.options.length && !plan.options.some((o) => o.recommended)) plan.options[0].recommended = true; touch(); drawList(); } }, '✕')),
          h('div.fields',
            field('Short description', input('text', option.tagline, set('tagline'))),
            h('div.grid4',
              field('Appliance', select(option.appliance, APPLIANCES, set('appliance'))),
              num('duration_months', 'Duration, months', { min: 1, max: 60 }),
              num('price', `Price, ${store.settings.currency}`, { step: 1000 }),
              num('down_payment', 'First payment', { step: 1000 })),
            h('div.grid4',
              num('installment_months', 'Installments, months', { max: 60 }),
              num('full_discount_pct', 'Pay-in-full discount, %', { max: 50 }),
              num('align_pct', 'Alignment corrected, %', { max: 100, step: 5 }),
              num('bite_pct', 'Bite corrected, %', { max: 100, step: 5 })),
            h('div.price-preview', { dataset: { pricing: option.id } }, pricingText(ctx.c.pricing[option.id])),
            h('div.grid2',
              field('Included (one per line)', h('textarea', { rows: 3, value: option.includes.join('\n'), oninput: (e) => { option.includes = e.target.value.split('\n').map((x) => x.trim()).filter(Boolean); touch(); } })),
              field('Why this option suits the patient', h('textarea', { rows: 3, value: option.note, oninput: (e) => { option.note = e.target.value; touch(); } }))),
          ));
      }));
    };
    drawList();
    const add = h('select', { onchange: (e) => {
      const item = store.settings.catalog.find((x) => x.id === e.target.value);
      e.target.value = '';
      if (!item) return;
      if (plan.options.length >= 4) { toast('A plan can offer up to four options.', 'error'); return; }
      const option = fromCatalog(item);
      plan.options.push(option);
      ctx.optionId = ctx.optionId || option.id;
      touch(); drawList();
    } }, h('option', { value: '' }, 'Add option from catalog…'), store.settings.catalog.map((item) => h('option', { value: item.id }, `${item.name.en || item.id} — ${money(item.price)}`)));
    return h('section.card', h('div.card-head', h('h2', 'Treatment options & pricing'), add), list);
  }

  // -- delivery tab ---------------------------------------------------------

  function deliveryTab() {
    const c = ctx.c;
    const post = (path, payload) => act(async () => {
      const data = await api('POST', `/api/cases/${id}/${path}`, payload);
      await refresh(data.case || data);
      if (data.result && !data.result.ok) toast(data.result.error, 'error');
    });
    const copyBox = (value) => h('div.linkbox', h('input', { type: 'text', readOnly: true, value, onfocus: (e) => e.target.select() }),
      h('button.btn.small', { type: 'button', onclick: guard(async () => { await navigator.clipboard.writeText(value); toast('Link copied.'); }) }, 'Copy'));

    const booking = c.booking;
    const summary = h('section.card', h('h2', 'Status'), h('dl.facts',
      h('dt', 'Status'), h('dd', h(`span.chip.${c.status}`, STATUS[c.status])),
      h('dt', 'Sent'), h('dd', when(c.sent_at)),
      h('dt', 'First opened'), h('dd', c.viewed_at ? `${when(c.viewed_at)} · ${c.view_count} view${c.view_count === 1 ? '' : 's'}` : 'Not yet'),
      c.status === 'booked' && booking && [h('dt', 'Chosen'), h('dd', `${booking.option_name} · ${booking.payment === 'full' ? 'pays in full' : 'installments'} · ${money(booking.amount)}`)],
      c.status === 'booked' && booking && (booking.preferred_days.length > 0 || booking.preferred_time) && [h('dt', 'Prefers'), h('dd', [booking.preferred_days.join(', '), booking.preferred_time].filter(Boolean).join(' · '))],
      c.status === 'booked' && booking && booking.note && [h('dt', 'Note'), h('dd', booking.note)],
      c.status === 'declined' && [h('dt', 'Reason'), h('dd', REASONS[c.decline_reason] || '—')],
    ),
    h('div.row', { style: 'margin-top:14px' },
      !['booked', 'declined', 'expired'].includes(c.status) && h('button.btn.small', { type: 'button', onclick: post('status', { status: 'booked' }) }, 'Mark as booked'),
      !['booked', 'declined', 'expired', 'draft'].includes(c.status) && h('button.btn.small', { type: 'button', onclick: post('status', { status: 'declined' }) }, 'Mark as declined'),
      ['booked', 'declined', 'expired'].includes(c.status) && h('button.btn.small', { type: 'button', onclick: post('status', { status: 'reopen' }) }, 'Reopen case'),
    ));

    const simulated = c.channel_mode === 'simulated';
    const links = h('section.card', h('h2', `${CHANNELS[c.channel]} delivery`), h('div.stack',
      simulated && h('div.notice', `${CHANNELS[c.channel]} is in simulated mode: messages are recorded below but nothing is sent to the patient. Add the ${CHANNELS[c.channel]} credentials to go live (see Settings).`),
      c.opted_out && h('div.notice', 'This patient asked not to be contacted. Automated messages are off.'),
      h('div', h('h3', 'Patient’s plan link'), h('p.muted.small', 'Anyone with this link can open the plan, so share it only with the patient.'), h('div', { style: 'margin-top:8px' }, copyBox(c.plan_link))),
      c.status === 'draft' ? h('p.muted.small', 'The link starts working once the plan is approved.') : h('img.qr', { src: `/api/cases/${id}/qr.svg?target=plan`, alt: 'QR code for the plan link' }),
      c.channel === 'telegram' && !simulated && h('div',
        h('h3', c.tg_linked ? 'Telegram: connected' : 'Telegram: waiting for the patient'),
        h('p.muted.small', c.tg_linked ? 'The patient has opened your bot; messages will reach them.' : 'A bot cannot message someone first. Ask the patient to scan this code or open the link once — the plan is then delivered automatically.'),
        c.telegram_link && h('div', { style: 'margin-top:8px' }, copyBox(c.telegram_link)),
        c.telegram_link && !c.tg_linked && h('img.qr', { style: 'margin-top:8px', src: `/api/cases/${id}/qr.svg?target=telegram`, alt: 'QR code to connect on Telegram' })),
    ));

    const open = ['sent', 'viewed'].includes(c.status);
    const stateText = { sent: 'Sent', next: 'Next', upcoming: 'Planned', on_hold: 'On hold', cancelled: 'Not needed' };
    const followups = h('section.card',
      h('div.card-head', h('h2', 'Automatic follow-ups'), open && h('div.row',
        h('button.btn.small', { type: 'button', onclick: post('pause', { paused: !c.followups_paused }) }, c.followups_paused ? 'Resume' : 'Pause'),
        c.schedule.some((s) => ['next', 'on_hold'].includes(s.state)) && h('button.btn.small', { type: 'button', onclick: post('followup-now') }, 'Send next now'))),
      c.schedule.length === 0
        ? h('p.muted', `Reminders start after the plan is sent: on day ${store.settings.followup_days.join(', ')}. They stop the moment the patient books, declines or replies STOP.`)
        : h('div.stack',
          open && c.pending_question && h('div.notice', 'On hold: the patient asked a question. Reminders resume a day after you reply.'),
          open && c.followups_paused && h('div.notice', 'Paused. No reminders will be sent until you resume.'),
          h('ol.steps', c.schedule.map((s) => h(`li.${s.state}`, h('span.mark', s.state === 'sent' ? '✓' : s.step), h('span', `Day ${s.day} reminder`),
            h('span.when', s.due_at && s.state !== 'sent' ? `${stateText[s.state]} · ${when(s.due_at)}` : stateText[s.state])))),
          h('p.muted.small', `Sent only between ${store.settings.send_window[0]}:00 and ${store.settings.send_window[1]}:00 (${store.settings.timezone}).`)),
    );

    const replyBox = h('textarea', { placeholder: 'Write a reply to the patient…', 'aria-label': 'Reply to the patient' });
    const thread = h('div.thread', c.messages.length === 0 ? h('p.muted', 'No messages yet.') : c.messages.map((m) => h(`div.bubble.${m.direction}`, m.body,
      h('span.meta', `${when(m.created_at)} · ${m.direction === 'in' ? (m.channel === 'page' ? 'from the plan page' : `via ${CHANNELS[m.channel] || m.channel}`) : m.status}`,
        m.error && h('span.bad', ` — ${m.error}`)))));
    const conversation = h('section.card', h('div.card-head', h('h2', 'Conversation'), c.pending_question && h('span.chip.flag', 'Waiting for your reply')), thread,
      h('div.reply', replyBox, h('button.btn.primary', { type: 'button', onclick: () => post('reply', { text: replyBox.value })() }, 'Send')));
    queueMicrotask(() => { thread.scrollTop = thread.scrollHeight; });

    const log = h('section.card', h('h2', 'Activity'), h('ul.log', [...c.events].reverse().map((e) =>
      h('li', h('span', `${EVENTS[e.type] || e.type}${e.data && e.data.detail ? `: ${e.data.detail}` : ''}${e.data && e.data.step ? ` (${e.data.step})` : ''}`), h('time', when(e.created_at))))));

    return h('div.delivery-grid', h('div.stack', summary, followups, log), h('div.stack', conversation, links));
  }

  function drawTab() {
    if (ctx.sim && ctx.sim.sim) ctx.sim.sim.dispose();
    ctx.sim = null;
    put(body, ctx.tab === 'plan'
      ? h('div.plan-grid', h('div.stack', patientCard(), notesCard(), recordsCard()), h('div.stack', simulatorCard(), textCard(), optionsCard()))
      : deliveryTab());
  }

  add(view, 
    h('div.case-head', h('a.back', { href: '#/' }, '← Cases'), title, statusChip, h('div.spacer'), saveState, headActions,
      h('button.icon-btn', { type: 'button', title: 'Delete case', 'aria-label': 'Delete case', onclick: guard(async () => {
        if (!window.confirm(`Delete the case for ${ctx.c.patient_name}? Its images and messages are removed too.`)) return;
        ctx.dirty = false;
        await api('DELETE', `/api/cases/${id}`);
        location.hash = '#/';
      }) }, '🗑')),
    tabs, body);
  drawHead();
  drawTab();

  // Keep the delivery view current while it is open (views, replies, bookings).
  const poll = setInterval(guard(async () => {
    if (ctx.tab !== 'delivery' || ctx.dirty || document.activeElement?.tagName === 'TEXTAREA') return;
    const fresh = await api('GET', `/api/cases/${id}`);
    if (fresh.updated_at !== ctx.c.updated_at || fresh.messages.length !== ctx.c.messages.length || fresh.events.length !== ctx.c.events.length) await refresh(fresh);
  }), 10000);
  leave.push(() => { clearInterval(poll); clearTimeout(ctx.timer); if (ctx.dirty) save().catch(() => {}); if (ctx.sim && ctx.sim.sim) ctx.sim.sim.dispose(); });
}

// ---------------------------------------------------------------- settings

async function viewSettings() {
  await loadSettings();
  const view = shell('settings');
  const s = JSON.parse(JSON.stringify(store.settings));
  const set = (key) => (v) => { s[key] = v; };
  const catalogBox = h('div.stack');

  const langRows = (object, multiline) => h('div.lang-rows', Object.keys(LANGS).map((lang) => h('div.lang-row', h('b', lang.toUpperCase()),
    multiline
      ? h('textarea', { rows: 3, value: (object[lang] || []).join('\n'), oninput: (e) => { object[lang] = e.target.value.split('\n').map((x) => x.trim()).filter(Boolean); } })
      : h('input', { type: 'text', value: object[lang] || '', oninput: (e) => { object[lang] = e.target.value; } }))));

  const drawCatalog = () => {
    put(catalogBox, s.catalog.map((item, index) => {
      const num = (key, label, extra = {}) => field(label, input('number', item[key], (v) => { item[key] = v; }, { min: 0, ...extra }));
      return h('div.catalog-item',
        h('div.card-head', h('h3', item.name.en || item.id), h('button.btn.small.danger', { type: 'button', onclick: () => { s.catalog.splice(index, 1); drawCatalog(); } }, 'Remove')),
        h('div.grid3', field('Appliance shown in 3D', select(item.appliance, APPLIANCES, (v) => { item.appliance = v; })), num('price', `Price, ${s.currency}`, { step: 1000 }), num('months', 'Typical duration, months', { min: 1 })),
        h('div.grid3', num('down_pct', 'First payment, %', { max: 100 }), num('installment_months', 'Installments, months', { max: 60 }), num('full_discount_pct', 'Pay-in-full discount, %', { max: 50 })),
        field('Name', langRows(item.name)), field('Short description', langRows(item.tagline)), field('Included (one per line)', langRows(item.includes, true)));
    }));
  };
  drawCatalog();

  const st = store.status;
  const integration = (name, on, text, how) => h('div.integration', h('strong', name), h(`span.chip.${on ? 'booked' : 'draft'}`, text), h('p.muted.small', { style: 'grid-column:1/-1' }, how));
  const code = (text) => h('code', text);

  add(view, 
    h('div.page-head', h('h1', 'Settings'), h('button.btn.primary', { type: 'button', onclick: guard(async () => {
      s.followup_days = String(s.followup_days_text ?? s.followup_days.join(', ')).split(/[,\s]+/).map(Number).filter((n) => n > 0);
      const saved = await api('PUT', '/api/settings', s);
      store.settings = saved.settings;
      toast('Settings saved.');
      viewSettings();
    }) }, 'Save settings')),
    h('div.settings-grid',
      h('div.stack',
        h('section.card', h('h2', 'Clinic'), h('div.stack',
          field('Clinic name (shown to patients)', input('text', s.clinic_name, set('clinic_name'))),
          field('Treating doctor', input('text', s.dentist_name, set('dentist_name'), { placeholder: 'Dr. …' })),
          h('div.grid2', field('Phone', input('tel', s.phone, set('phone'))), field('Address', input('text', s.address, set('address')))),
          h('div.grid3',
            field('Currency', select(s.currency, Object.fromEntries(Object.entries(store.currencies).map(([k, v]) => [k, `${k} ${v.symbol}`])), set('currency'))),
            field('Default patient language', select(s.default_language, LANGS, set('default_language'))),
            field('Country calling code', input('text', s.country_code, set('country_code')))),
        )),
        h('section.card', h('h2', 'Follow-up automation'), h('div.stack',
          field('Reminder days after sending (comma separated)', input('text', s.followup_days.join(', '), (v) => { s.followup_days_text = v; })),
          h('div.grid3',
            field('Send from (hour)', input('number', s.send_window[0], (v) => { s.send_window[0] = v; }, { min: 0, max: 23 })),
            field('Send until (hour)', input('number', s.send_window[1], (v) => { s.send_window[1] = v; }, { min: 1, max: 24 })),
            field('Time zone', input('text', s.timezone, set('timezone')))),
          h('div.grid2',
            field('Close as “no response” after last reminder, days', input('number', s.expire_after_days, set('expire_after_days'), { min: 1 })),
            field('Prices valid for, days', input('number', s.plan_valid_days, set('plan_valid_days'), { min: 1 }))),
          h('p.muted.small', 'Reminders stop automatically when the patient books, declines, or replies STOP. They pause while a patient’s question is waiting for your answer.'),
        )),
        h('section.card', h('h2', 'Connections'),
          integration('AI plan drafting', st.ai.enabled, st.ai.enabled ? `On · ${st.ai.model}` : 'Off', ['Set ', code('ANTHROPIC_API_KEY'), ' and restart. Without it, drafts are built from keywords in your notes.']),
          integration('WhatsApp', st.whatsapp.mode === 'live', st.whatsapp.mode === 'live' ? 'Live' : 'Simulated', ['WhatsApp Cloud API: ', code('WHATSAPP_TOKEN'), ', ', code('WHATSAPP_PHONE_NUMBER_ID'), ', ', code('WHATSAPP_APP_SECRET'), ', ', code('WHATSAPP_VERIFY_TOKEN'), ' and approved templates ', code('WHATSAPP_TEMPLATE_PLAN'), ' / ', code('WHATSAPP_TEMPLATE_FOLLOWUP'), '.',
            st.whatsapp.mode === 'live' && !(st.whatsapp.template_plan && st.whatsapp.template_followup) ? ' Template names are missing — first messages will fail until they are set.' : '']),
          integration('Telegram', st.telegram.mode === 'live', st.telegram.mode === 'live' ? `Live${st.telegram.bot_username ? ` · @${st.telegram.bot_username}` : ''}` : 'Simulated', ['Create a bot with @BotFather and set ', code('TELEGRAM_BOT_TOKEN'), '. Optional staff alerts: ', code('TELEGRAM_STAFF_CHAT_ID'), '.']),
          integration('Patient links', st.public_url.startsWith('https://'), st.public_url, ['Set ', code('ORTHO_PUBLIC_URL'), ' to the public https address of this server so the links in messages work on patients’ phones.']),
        ),
      ),
      h('section.card', h('div.card-head', h('h2', 'Treatment catalog & prices'),
        h('button.btn.small', { type: 'button', onclick: () => {
          const blank = () => ({ hy: '', ru: '', en: '' });
          s.catalog.push({ id: `item${Date.now().toString(36)}`, appliance: 'metal', price: 0, months: 18, down_pct: 25, installment_months: 12, full_discount_pct: 0, name: { ...blank(), en: 'New treatment' }, tagline: blank(), includes: { hy: [], ru: [], en: [] } });
          drawCatalog();
        } }, 'Add treatment')),
      h('p.muted.small', { style: 'margin-bottom:12px' }, 'These are the defaults used when a plan is drafted. The starting prices are placeholders — replace them with your own.'),
      catalogBox),
    ),
  );
}

// ---------------------------------------------------------------- router

async function route() {
  teardown();
  const hash = location.hash || '#/';
  try {
    const match = hash.match(/^#\/case\/(\d+)/);
    if (match) await viewCase(Number(match[1]));
    else if (hash.startsWith('#/settings')) await viewSettings();
    else await viewCases();
  } catch (error) {
    if (error.message !== 'Signed out') toast(error.message, 'error');
  }
}

async function boot() {
  const me = await api('GET', '/api/me');
  if (!me.authenticated) { showLogin(); return; }
  await loadSettings();
  await route();
}

window.addEventListener('hashchange', route);
boot();
