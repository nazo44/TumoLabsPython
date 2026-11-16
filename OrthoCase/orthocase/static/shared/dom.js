// Tiny DOM helpers shared by the clinic dashboard and the patient page.

// h('button.btn.primary', { onclick, disabled }, 'Save')
export function h(spec, props, ...children) {
  const [tag, ...classes] = spec.split('.');
  const node = document.createElement(tag || 'div');
  if (classes.length) node.className = classes.join(' ');
  if (props && (typeof props !== 'object' || props instanceof Node || Array.isArray(props))) {
    children.unshift(props);
    props = null;
  }
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key in node && key !== 'list') node[key] = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  append(node, children);
  return node;
}

function append(node, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) append(node, child);
    else node.append(child instanceof Node ? child : String(child));
  }
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

// Like node.append(), but skips false/null and flattens arrays, as h() does.
export function add(node, ...children) {
  append(node, children);
  return node;
}

// Replace a node's children.
export function put(node, ...children) {
  return add(clear(node), ...children);
}

// "600 000 ֏" / "$1,200" — symbol placement follows the currency's convention.
export function formatMoney(amount, currency, symbol) {
  const digits = String(Math.round(Number(amount) || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  return currency === 'USD' || currency === 'EUR' ? `${symbol}${digits}` : `${digits} ${symbol}`;
}

export function formatDate(iso) {
  if (!iso) return '';
  const [y, m, d] = iso.slice(0, 10).split('-');
  return `${d}.${m}.${y}`;
}

export function fill(template, values) {
  return template.replace(/\{(\w+)\}/g, (_, key) => (key in values ? values[key] : ''));
}
