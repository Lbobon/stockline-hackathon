// Builds the prototype's customer list from the ERP seed data (data/seed/*.json, git-ignored Stockline data),
// so the agent calls the ERP's real clients with their real usual order and a real near-expiry offer.
// Nothing here is committed as data: it is computed at read time. Returns null when the seed is absent.

import fs from 'node:fs';
import path from 'node:path';

const MARGIN_FLOOR = 0.05; // never offer a discount that leaves less than 5% margin on cost
const DISCOUNT = 0.15;
const RECENT_ORDERS = 10; // orders looked at to guess "the usual"
const USUAL_LINES = 4;

const read = (dir, name) => JSON.parse(fs.readFileSync(path.join(dir, name), 'utf8'));
const median = (xs) => { const s = [...xs].sort((a, b) => a - b); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const days = (from, to) => Math.round((Date.parse(to) - Date.parse(from)) / 86400000);
const fmtQty = (q) => String(Math.round(q * 10) / 10).replace('.', ',');

function toE164(phone) {
  const d = String(phone || '').replace(/\D/g, '');
  if (/^0[1-9]\d{8}$/.test(d)) return `+33${d.slice(1)}`;
  return '';
}

function describeLine(product, qty) {
  const q = fmtQty(qty);
  if (product.unit === 'kg') return `${q} kg de ${product.name}`;
  return `${q} × ${product.name}`; // piece / box
}

export function loadErpCustomers(seedDir, { withPhones = false } = {}) {
  let clients, orders, products, lots, today;
  try {
    clients = read(seedDir, 'clients.json');
    orders = read(seedDir, 'orders.json');
    products = read(seedDir, 'products.json');
    lots = read(seedDir, 'lots.json');
    today = read(seedDir, 'meta.json').today;
  } catch {
    return null;
  }

  const productById = new Map(products.map((p) => [p.id, p]));
  const ordersByClient = new Map();
  for (const o of orders) {
    if (o.status === 'cancelled' || o.status === 'draft') continue;
    if (!ordersByClient.has(o.clientId)) ordersByClient.set(o.clientId, []);
    ordersByClient.get(o.clientId).push(o);
  }

  // Lots still on the shelf that expire within 3 days, soonest first: what we want to push today.
  const expiring = lots
    .filter((l) => l.qtyRemaining > 0 && l.expiresAt >= today && days(today, l.expiresAt) <= 3)
    .map((l) => ({ lot: l, product: productById.get(l.productId) }))
    .filter((x) => x.product && x.product.active)
    .sort((a, b) => a.lot.expiresAt.localeCompare(b.lot.expiresAt));

  const offerFor = (usualIds) => {
    // Prefer something in a category the client already buys, else the soonest-expiring lot.
    const cats = new Set(usualIds.map((id) => productById.get(id)?.category));
    const pick = expiring.find((x) => cats.has(x.product.category) && !usualIds.includes(x.product.id)) || expiring.find((x) => !usualIds.includes(x.product.id));
    if (!pick) return '';
    const { product, lot } = pick;
    const left = days(today, lot.expiresAt);
    const when = left <= 0 ? "à écouler aujourd'hui" : left === 1 ? 'à écouler sous 24 h' : `à écouler sous ${left} jours`;
    const price = Number(product.price) * (1 - DISCOUNT);
    const discounted = Number(product.cost) && price >= Number(product.cost) * (1 + MARGIN_FLOOR);
    const unit = product.unit === 'kg' ? 'le kg' : "l'unité";
    return discounted
      ? `${product.name}, ${when}, -${Math.round(DISCOUNT * 100)} % : ${price.toFixed(2).replace('.', ',')} € ${unit}`
      : `${product.name}, ${when}, ${Number(product.price).toFixed(2).replace('.', ',')} € ${unit}`;
  };

  return clients.filter((c) => c.status === 'active').map((c) => {
    const recent = (ordersByClient.get(c.id) || [])
      .sort((a, b) => b.deliveryDate.localeCompare(a.deliveryDate))
      .slice(0, RECENT_ORDERS);
    const byProduct = new Map();
    for (const o of recent) {
      for (const l of o.lines) {
        if (!productById.has(l.productId)) continue;
        if (!byProduct.has(l.productId)) byProduct.set(l.productId, []);
        byProduct.get(l.productId).push(l.qty);
      }
    }
    const usual = [...byProduct.entries()]
      .sort((a, b) => b[1].length - a[1].length)
      .slice(0, USUAL_LINES);
    const contact = (c.contacts || []).find((x) => /command/i.test(x.role || '')) || (c.contacts || [])[0] || {};
    return {
      id: c.id,
      restaurant_name: c.name,
      contact_name: String(contact.name || '').split(' ')[0] || '',
      phone: withPhones ? toE164(contact.phone) : '',
      usual_order: usual.map(([id, qtys]) => describeLine(productById.get(id), median(qtys))).join(', '),
      todays_offer: offerFor(usual.map(([id]) => id)),
    };
  });
}
