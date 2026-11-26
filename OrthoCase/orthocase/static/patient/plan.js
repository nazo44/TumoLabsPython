// The page a patient opens from WhatsApp or Telegram.

import { h, add, clear, put, formatMoney, formatDate, fill } from '../shared/dom.js';
import { mountSimulator } from '../sim/simulator.js';
import { STRINGS, LANG_NAMES } from './strings.js';

const token = location.pathname.split('/').filter(Boolean).pop();
const preview = new URLSearchParams(location.search).get('preview') === '1';
const app = document.getElementById('app');

const state = {
  data: null,
  lang: 'hy',
  optionId: null, // option shown in the 3D view and preselected when booking
  sim: null,
  sheet: null,
  sent: new Set(),
};

const t = (key, values) => fill(STRINGS[state.lang][key] || STRINGS.en[key] || key, values || {});

async function post(path, body) {
  const response = await fetch(`/api/public/${token}/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || 'error');
  }
  return response.json();
}

// Anonymous usage signals for the clinic ("opened", "played the 3D preview").
function track(type, detail) {
  if (preview || state.sent.has(type)) return;
  state.sent.add(type);
  post('event', { type, detail }).catch(() => {});
}

const money = (amount) => formatMoney(amount, state.data.clinic.currency, state.data.clinic.currency_symbol);
const currentOption = () => state.data.plan.options.find((o) => o.id === state.optionId) || state.data.plan.options[0];

// ---------------------------------------------------------------- render

function render() {
  const { data } = state;
  document.documentElement.lang = state.lang;
  document.title = `${t('eyebrow')} — ${data.clinic.name}`;
  if (state.sim && state.sim.sim) state.sim.sim.dispose();
  clear(app);

  const booked = data.status === 'booked';
  const declined = data.status === 'declined';

  add(app, 
    h('header.topbar',
      h('div.clinic', data.clinic.name),
      h('div.langs', { role: 'group' },
        Object.entries(LANG_NAMES).map(([code, label]) =>
          h('button', { type: 'button', 'aria-pressed': String(code === state.lang), onclick: () => { state.lang = code; render(); } }, label))),
    ),
    preview && h('p.preview-banner', 'Preview — this is what the patient will see. Buttons are switched off here.'),
    h('div.hero',
      h('p.eyebrow', t('eyebrow')),
      h('h1', t('greeting', { name: data.patient_name })),
      data.plan.summary && h('p.summary', data.plan.summary),
      h('div.meta',
        data.clinic.dentist && h('span', t('prepared_by', { dentist: data.clinic.dentist })),
        data.plan.valid_until && h('span', t('valid_until', { date: formatDate(data.plan.valid_until) })),
      ),
      booked && h('div.notice', h('h3', t('booked_title')), h('p', t('booked_text'))),
      declined && h('div.notice', h('p', t('decline_done'))),
    ),
    renderSimulator(),
    data.plan.findings.length > 0 && h('section',
      h('h2', t('findings_title')),
      h('div.findings', data.plan.findings.map((finding, index) =>
        h('div.card.finding', h('div.num', index + 1), h('div', h('h3', finding.title), finding.detail && h('p', finding.detail))))),
    ),
    data.images.length > 0 && h('section',
      h('h2', t('records_title')),
      h('div.gallery', data.images.map((image) =>
        h('button', { type: 'button', onclick: () => openLightbox(image) },
          h('img', { src: image.url, alt: image.caption || t(image.kind), loading: 'lazy' }),
          h('span.tag', image.caption || t(image.kind))))),
    ),
    data.plan.options.length > 0 && h('section', { id: 'options' },
      h('h2', t('options_title')),
      h('div.options', data.plan.options.map(renderOption)),
    ),
    !booked && !declined && h('p.later', h('button.btn.btn-quiet', { type: 'button', onclick: () => openSheet('decline') }, t('not_now'))),
    h('footer',
      h('p', h('strong', data.clinic.name)),
      data.clinic.address && h('p', data.clinic.address),
      data.clinic.phone && h('p', `${t('contact')}: `, h('a', { href: `tel:${data.clinic.phone.replace(/[^\d+]/g, '')}` }, data.clinic.phone)),
    ),
  );

  if (!booked) {
    add(app, h('div.actionbar', h('div.actionbar-inner',
      h('button.btn.btn-primary', { type: 'button', onclick: () => openSheet('book') }, t('book')),
      h('button.btn.btn-ghost', { type: 'button', onclick: () => openSheet('ask') }, t('ask')),
    )));
  }
  if (state.sheet) openSheet(state.sheet);
}

function renderSimulator() {
  const options = state.data.plan.options;
  const host = h('div');
  const section = h('section',
    h('h2', t('sim_title')),
    h('div.card.sim-card',
      options.length > 1 && h('div.sim-options', { role: 'group' }, options.map((option) =>
        h('button', {
          type: 'button', 'aria-pressed': String(option.id === state.optionId),
          onclick: () => { state.optionId = option.id; track('option_selected', option.name); syncSimulator(true); },
          dataset: { option: option.id },
        }, option.name))),
      host,
      h('p.sim-note', t('sim_note')),
    ),
  );
  state.simSection = section;
  // Mount after the node is in the document so the canvas gets a real size.
  queueMicrotask(() => {
    state.sim = mountSimulator(host, {
      labels: STRINGS[state.lang].sim,
      onInteract: (kind) => { if (kind === 'play' || kind === 'scrub') track('sim_played'); if (kind === 'compare') track('compare_used'); },
    });
    syncSimulator(false);
  });
  return section;
}

function syncSimulator(restart) {
  const option = currentOption();
  for (const button of state.simSection.querySelectorAll('[data-option]')) {
    button.setAttribute('aria-pressed', String(button.dataset.option === (option && option.id)));
  }
  const mounted = state.sim;
  if (!mounted || !mounted.sim) return;
  mounted.sim.setParams(state.data.plan.sim);
  if (option) {
    mounted.sim.setOutcome(option.align_pct, option.bite_pct);
    mounted.sim.setAppliance(option.appliance);
    mounted.setMonths(option.duration_months);
  }
  if (restart) { mounted.sim.setProgress(0); mounted.sim.play(); }
}

function renderOption(option) {
  const p = option.pricing;
  const booked = state.data.status === 'booked';
  return h(`article.card.option${option.recommended ? '.is-recommended' : ''}`,
    option.recommended && h('span.badge', t('recommended')),
    h('div.option-head', h('h3', option.name), h('span.option-duration', t('duration', { n: option.duration_months }))),
    option.tagline && h('p.option-tagline', option.tagline),
    option.note && h('p.option-note', option.note),
    h('div.price',
      h('div.price-total', h('span', t('total')), h('strong', money(p.total))),
      p.monthly > 0 && h('p.price-line', p.down > 0
        ? t('installments', { down: money(p.down), n: p.months, monthly: money(p.monthly) })
        : t('installments_no_down', { n: p.months, monthly: money(p.monthly) })),
      p.savings > 0 && h('p.price-line.alt', t('pay_full', { price: money(p.full_price), save: money(p.savings) })),
    ),
    option.includes.length > 0 && h('ul.includes', option.includes.map((item) => h('li', item))),
    h('div.option-actions',
      !booked && h('button.btn.btn-primary', { type: 'button', onclick: () => { state.optionId = option.id; syncSimulator(false); openSheet('book'); } }, t('choose')),
      h('button.btn.btn-ghost', {
        type: 'button',
        onclick: () => {
          state.optionId = option.id;
          track('option_selected', option.name);
          syncSimulator(true);
          state.simSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
        },
      }, t('view_3d')),
    ),
  );
}

// ---------------------------------------------------------------- sheets

function closeSheet() {
  state.sheet = null;
  document.querySelector('.sheet-backdrop')?.remove();
}

function openLightbox(image) {
  const box = h('figure.lightbox', { onclick: () => box.remove() },
    h('button', { type: 'button', 'aria-label': t('close') }, '×'),
    h('div', h('img', { src: image.url, alt: image.caption || t(image.kind) }), h('figcaption', image.caption || t(image.kind))));
  document.body.append(box);
}

function openSheet(kind) {
  closeSheet();
  state.sheet = kind;
  const body = h('div');
  const backdrop = h('div.sheet-backdrop', { onclick: (event) => { if (event.target === backdrop) closeSheet(); } },
    h('div.sheet', { role: 'dialog', 'aria-modal': 'true' }, body));
  document.body.append(backdrop);
  const head = (title) => h('div.sheet-head', h('h2', title), h('button.sheet-close', { type: 'button', 'aria-label': t('close'), onclick: closeSheet }, '×'));

  const done = (title, text, after) => {
    put(body, h('div.done', h('div.tick', '✓'), title && h('h2', title), h('p', text),
      h('button.btn.btn-primary', { type: 'button', onclick: () => { closeSheet(); if (after) after(); } }, t('close'))));
  };

  const submitter = (button, errorNode, action) => async () => {
    if (preview) { errorNode.textContent = 'Preview mode — nothing is sent.'; return; }
    button.disabled = true;
    errorNode.textContent = '';
    try { await action(); } catch (error) { errorNode.textContent = t('error'); button.disabled = false; }
  };

  if (kind === 'book') {
    const options = state.data.plan.options;
    const form = { option: (currentOption() || options[0]).id, payment: 'installments', days: new Set(), time: '', note: '' };
    const paymentBox = h('div.choices');
    const drawPayment = () => {
      const p = options.find((o) => o.id === form.option).pricing;
      if (!p.monthly) form.payment = 'full';
      put(paymentBox, 
        p.monthly > 0 && h('button.choice', { type: 'button', 'aria-pressed': String(form.payment === 'installments'), onclick: () => { form.payment = 'installments'; drawPayment(); } },
          t('pay_installments'),
          h('small', p.down > 0 ? t('installments', { down: money(p.down), n: p.months, monthly: money(p.monthly) }) : t('installments_no_down', { n: p.months, monthly: money(p.monthly) }))),
        h('button.choice', { type: 'button', 'aria-pressed': String(form.payment === 'full'), onclick: () => { form.payment = 'full'; drawPayment(); } },
          t('pay_once'), h('small', money(p.full_price))),
      );
    };
    drawPayment();
    const toggle = (button, on) => button.setAttribute('aria-pressed', String(on));
    const error = h('p.form-error', { role: 'alert' });
    const submit = h('button.btn.btn-primary', { type: 'button' }, t('book_submit'));
    submit.addEventListener('click', submitter(submit, error, async () => {
      await post('book', { option_id: form.option, payment: form.payment, preferred_days: [...form.days], preferred_time: form.time, note: form.note });
      state.data.status = 'booked';
      done(t('booked_title'), t('booked_text'), render);
    }));
    const times = ['morning', 'afternoon', 'evening'];
    const timeButtons = times.map((key) => h('button', { type: 'button', 'aria-pressed': 'false', onclick: () => { form.time = form.time === key ? '' : key; timeButtons.forEach((b, i) => toggle(b, times[i] === form.time)); } }, t(key)));
    add(body, 
      head(t('book_title')),
      h('div.field', h('label', { for: 'book-option' }, t('book_option')),
        h('select', { id: 'book-option', onchange: (event) => { form.option = event.target.value; drawPayment(); } },
          options.map((o) => h('option', { value: o.id, selected: o.id === form.option }, `${o.name} — ${money(o.pricing.total)}`)))),
      h('div.field', h('span.label', t('book_payment')), paymentBox),
      h('div.field', h('span.label', t('book_days')),
        h('div.chips', STRINGS[state.lang].days.map((label, index) => {
          const key = STRINGS.en.days[index];
          const button = h('button', { type: 'button', 'aria-pressed': 'false', onclick: () => { form.days.has(key) ? form.days.delete(key) : form.days.add(key); toggle(button, form.days.has(key)); } }, label);
          return button;
        }))),
      h('div.field', h('span.label', t('book_time')), h('div.chips', timeButtons)),
      h('div.field', h('label', { for: 'book-note' }, t('book_note')), h('textarea', { id: 'book-note', maxLength: 600, oninput: (event) => { form.note = event.target.value; } })),
      error, submit, h('p.hint', t('book_hint')),
    );
  } else if (kind === 'ask') {
    const input = h('textarea', { id: 'ask-text', maxLength: 2000, placeholder: t('ask_placeholder'), 'aria-label': t('ask_title') });
    const error = h('p.form-error', { role: 'alert' });
    const submit = h('button.btn.btn-primary', { type: 'button' }, t('send'));
    submit.addEventListener('click', submitter(submit, error, async () => {
      if (!input.value.trim()) { submit.disabled = false; input.focus(); return; }
      await post('question', { text: input.value });
      done('', t('ask_done'));
    }));
    add(body, head(t('ask_title')), h('div.field', input), error, submit);
    input.focus();
  } else {
    const reasons = ['price', 'timing', 'other_clinic', 'not_interested', 'other'];
    const form = { reason: '', note: '' };
    const buttons = reasons.map((key) => h('button.choice', { type: 'button', 'aria-pressed': 'false', onclick: () => { form.reason = key; buttons.forEach((b, i) => b.setAttribute('aria-pressed', String(reasons[i] === key))); } }, t(`reason_${key}`)));
    const error = h('p.form-error', { role: 'alert' });
    const submit = h('button.btn.btn-primary', { type: 'button' }, t('send'));
    submit.addEventListener('click', submitter(submit, error, async () => {
      await post('decline', { reason: form.reason || 'other', note: form.note });
      state.data.status = 'declined';
      done('', t('decline_done'), render);
    }));
    add(body, 
      head(t('decline_title')), h('p', t('decline_text')),
      h('div.field', h('div.choices', buttons)),
      h('div.field', h('label', { for: 'decline-note' }, t('comment')), h('textarea', { id: 'decline-note', maxLength: 600, oninput: (event) => { form.note = event.target.value; } })),
      error, submit,
    );
  }
}

document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  document.querySelector('.lightbox')?.remove();
  closeSheet();
});

// ---------------------------------------------------------------- start

async function start() {
  const browserLang = (navigator.language || 'hy').slice(0, 2);
  state.lang = STRINGS[browserLang] ? browserLang : 'hy';
  let response;
  try {
    response = await fetch(`/api/public/${token}${preview ? '?preview=1' : ''}`, { credentials: 'same-origin' });
  } catch (error) {
    response = null;
  }
  if (!response || !response.ok) {
    put(app, h('div.empty', h('h1', t('not_found_title')), h('p', t('not_found_text'))));
    return;
  }
  state.data = await response.json();
  state.lang = STRINGS[state.data.language] ? state.data.language : 'hy';
  const options = state.data.plan.options;
  const initial = options.find((o) => o.id === state.data.booked_option) || options.find((o) => o.recommended) || options[0];
  state.optionId = initial ? initial.id : null;
  render();
  track('view');
}

start();
