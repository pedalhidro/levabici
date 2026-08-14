'use strict';

/* levabici — a bici no transporte coletivo.
 *
 * Arquitetura: PWA estática sem build. Todo o estado é um grafo RDF:
 *   - data/vocab.ttl    — ontologia (classes, propriedades, escalas SKOS)
 *   - data/reviews.ttl  — grafo semente publicado (empresas + avaliações)
 *   - localStorage      — avaliações criadas neste aparelho, como Turtle
 * Os três são parseados (N3) num único N3.Store em memória; a interface
 * é toda derivada dele. O formulário espelha as severidades do
 * data/shapes.ttl (FORM_CONSTRAINTS abaixo): Violation bloqueia,
 * Warning avisa, Info não interfere.
 */

// ===================== vocabulário =====================

const NS = {
  lb: 'https://id.pedalhidrografi.co/levabici/terms#',
  emp: 'https://id.pedalhidrografi.co/levabici/empresa/',
  av: 'https://id.pedalhidrografi.co/levabici/avaliacao/',
  schema: 'https://schema.org/',
  prov: 'http://www.w3.org/ns/prov#',
  rdf: 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
  rdfs: 'http://www.w3.org/2000/01/rdf-schema#',
  skos: 'http://www.w3.org/2004/02/skos/core#',
  xsd: 'http://www.w3.org/2001/XMLSchema#',
};

const { namedNode, literal, quad } = N3.DataFactory;
const T = (prefix, local) => namedNode(NS[prefix] + local);
const RDF_TYPE = T('rdf', 'type');

// Escala de nota 1→5: vermelho (pior) → verde (melhor), interpolada em
// OKLCH (percepualmente uniforme) com LUMINOSIDADE MONOTÔNICA — mesmo
// sem distinguir vermelho de verde, a ordem continua legível pelo
// claro/escuro. Extremos validados nos dois temas (validate_palette.js
// do método de dataviz); a cor nunca aparece sem o número junto.
const SCORE_COLORS = { 1: '#90272d', 2: '#a35303', 3: '#a37d1e', 4: '#9fa531', 5: '#6bc87b' };

const MODE_META = {
  [NS.lb + 'modeBus']: { icon: 'bus', tripClass: NS.schema + 'BusTrip' },
  [NS.lb + 'modePlane']: { icon: 'plane', tripClass: NS.schema + 'Flight' },
  [NS.lb + 'modeTrain']: { icon: 'train', tripClass: NS.schema + 'TrainTrip' },
  [NS.lb + 'modeFerry']: { icon: 'ferry', tripClass: NS.schema + 'BoatTrip' },
  [NS.lb + 'modeOther']: { icon: 'van', tripClass: NS.schema + 'Trip' },
};

// Ícones-silhueta inline (fill: currentColor) — o app não usa emojis
// coloridos por decisão de estilo; mesmos traços da arte do logo.
const ICONS = {
  bus: '<path d="M4 2h16a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2h-1v2a1 1 0 0 1-2 0v-2H7v2a1 1 0 0 1-2 0v-2H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Zm1 4v6h14V6H5Zm1.5 8.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Zm11 0a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Z"/>',
  plane: '<path d="M21 15.5v-2l-8-5V3.6A1.6 1.6 0 0 0 11.5 2 1.6 1.6 0 0 0 10 3.6v4.9l-8 5v2l8-2.4v5.4l-2 1.5V22l3.5-1 3.5 1v-2l-2-1.5v-5.4l8 2.4Z"/>',
  train: '<path d="M12 2c-4.4 0-8 .6-8 4v9.5A3.5 3.5 0 0 0 7.5 19L6 20.5v.5h2.2l2-2h3.6l2 2H18v-.5L16.5 19A3.5 3.5 0 0 0 20 15.5V6c0-3.4-3.6-4-8-4ZM6 7h12v5H6V7Zm2.5 9.5a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3Zm7 0a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3Z"/>',
  ferry: '<path d="M10 2h4v3h3l1 6.2 2.6 1.1-1.8 4.4a4.6 4.6 0 0 1-2.3-1.2 4.3 4.3 0 0 1-3 1.3 4.3 4.3 0 0 1-3-1.3 4.3 4.3 0 0 1-3 1.3 4.6 4.6 0 0 1-2.4-.7L3.4 12.3 6 11.2 7 5h3V2Zm-2 5-.6 3.6L12 9l4.6 1.6L16 7H8Z"/><path d="M2 19c1.4 0 2.4-.5 3.5-1.2 1 .7 2.1 1.2 3.5 1.2s2.4-.5 3.5-1.2c1 .7 2.1 1.2 3.5 1.2s2.4-.5 3.5-1.2l1.5.9V21c-1.8 0-3.4-.4-5-1-1.6.6-3.4 1-5 1s-3.4-.4-5-1c-1.6.6-3.2 1-5 1v-2h1Z"/>',
  van: '<path d="M2 5h13l7 6v6h-2.3a2.6 2.6 0 0 1-5 0h-5.4a2.6 2.6 0 0 1-5 0H2V5Zm2 2v5h8V7H4Zm10 0v5h5.6L14 7Z"/>',
  trophy: '<path d="M6 2h12v2h4v2.5A4.5 4.5 0 0 1 17.4 11 6.5 6.5 0 0 1 13 14.9V18h4v2H7v-2h4v-3.1A6.5 6.5 0 0 1 6.6 11 4.5 4.5 0 0 1 2 6.5V4h4V2ZM4 6v.5A2.5 2.5 0 0 0 6 9V6H4Zm16 0h-2v3a2.5 2.5 0 0 0 2-2.5V6Z"/>',
  map: '<path d="M9 2 3 4v18l6-2 6 2 6-2V2l-6 2-6-2Zm1 2.7 4 1.3v13.3l-4-1.3V4.7Z"/>',
  sparkle: '<path d="M12 1.5l2.3 8.2 8.2 2.3-8.2 2.3L12 22.5l-2.3-8.2-8.2-2.3 8.2-2.3L12 1.5Z"/>',
  trash: '<path d="M9 2h6l1 2h5v2H3V4h5l1-2ZM5 8h14l-1.2 14H6.2L5 8Zm4 3v8h2v-8H9Zm4 0v8h2v-8h-2Z"/>',
  calendar: '<path d="M7 1v2H5a2 2 0 0 0-2 2v15a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2h-2V1h-2v2H9V1H7ZM5 9h14v11H5V9Z"/>',
};

function icon(name) {
  return (
    `<svg class="icon" viewBox="0 0 24 24" fill="currentColor" ` +
    `fill-rule="evenodd" aria-hidden="true">${ICONS[name]}</svg>`
  );
}

function modeIcon(modeIri) {
  return icon((MODE_META[modeIri] || MODE_META[NS.lb + 'modeOther']).icon);
}

// Perguntas do "como foi": propriedade lb: → esquema SKOS no vocab.ttl.
// Rótulos e opções vêm do próprio grafo (rdfs:label / skos:prefLabel /
// lb:ordinal) — o vocab.ttl é a fonte única.
const QUESTIONS = [
  { prop: 'stressLevel', scheme: 'StressLevelScheme', short: 'estresse' },
  { prop: 'permissionNeeded', boolean: true, short: 'permissão' },
  { prop: 'disassemblyLevel', scheme: 'DisassemblyScheme', short: 'desmontar' },
  { prop: 'packingLevel', scheme: 'PackingScheme', short: 'embalar' },
  { prop: 'receiptRequirement', scheme: 'ReceiptScheme', short: 'nota' },
  { prop: 'paymentLevel', scheme: 'PaymentScheme', short: 'pagar' },
];

const FRICTION_COLORS = ['var(--friction-0)', 'var(--friction-1)', 'var(--friction-2)'];

// Espelho em JS das severidades do data/shapes.ttl (fonte de verdade).
const FORM_CONSTRAINTS = {
  violation: ['empresa', 'nota'],
  warning: ['quando', ...QUESTIONS.map((q) => q.prop)],
  info: ['partida', 'chegada', 'fotos', 'comentário'],
};

const LOCAL_KEY = 'levabici:avaliacoes:v1';

// ===================== estado =====================

const store = new N3.Store(); // grafo completo (vocab + publicado + local)
const localStore = new N3.Store(); // só o que ainda não foi publicado
let map = null;
let mapLayer = null;
let pendingPhotos = []; // data URLs das fotos do formulário
let warningsConfirmed = false;
let apiAvailable = false; // backend (Cloud Run / Flask local) alcançável?
let companySelect = null; // instância TomSelect do seletor de empresa
let vocabQuads = []; // cache do vocab pra reconstruir o grafo sem re-fetch
let serverText = ''; // último Turtle publicado que buscamos
let editingSlug = null; // avaliação em edição (rota #/editar/<slug>)

// ===================== helpers RDF =====================

function parseTurtle(ttl) {
  return new N3.Parser().parse(ttl);
}

function obj(subject, predicate) {
  const os = store.getObjects(subject, predicate, null);
  return os.length ? os[0] : null;
}

function lit(subject, predicate) {
  const o = obj(subject, predicate);
  return o && o.termType === 'Literal' ? o.value : null;
}

function prefLabel(concept) {
  return lit(concept, T('skos', 'prefLabel')) || concept.value.split('#').pop();
}

function serializeQuads(quads) {
  return new Promise((resolve, reject) => {
    const writer = new N3.Writer({
      prefixes: {
        lb: NS.lb, emp: NS.emp, av: NS.av, schema: NS.schema,
        prov: NS.prov, xsd: NS.xsd,
      },
    });
    writer.addQuads(quads);
    writer.end((err, result) => (err ? reject(err) : resolve(result)));
  });
}

// Reconstrói o grafo em memória a partir das três fontes. O grafo
// publicado é sempre substituído inteiro — edição/apagamento remoto
// ficam simples e o histórico fica no versionamento do bucket.
function rebuildStore() {
  store.removeQuads(store.getQuads(null, null, null, null));
  store.addQuads(vocabQuads);
  if (serverText) store.addQuads(parseTurtle(serverText));
  store.addQuads(localStore.getQuads(null, null, null, null));
}

async function refreshGraph() {
  const res = await fetch('data/reviews.ttl', { cache: 'no-store' });
  serverText = await res.text();
  rebuildStore();
}

async function apiFetch(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = '';
    try {
      const j = await res.json();
      detail = j.violations ? j.violations.join('; ') : j.error || j.message || '';
    } catch (e) {
      /* corpo não-JSON */
    }
    throw new Error(`HTTP ${res.status}${detail ? ' — ' + detail : ''}`);
  }
  return res;
}

function reviewSubtreeQuads(fromStore, reviewIri) {
  const prefix = reviewIri.value + '_';
  return fromStore
    .getQuads(null, null, null, null)
    .filter((q) => q.subject.value === reviewIri.value || q.subject.value.startsWith(prefix));
}

async function persistLocal() {
  const ttl = await serializeQuads(localStore.getQuads(null, null, null, null));
  localStorage.setItem(LOCAL_KEY, ttl);
}

// ===================== modelo =====================

function schemeOptions(schemeLocal) {
  const concepts = store.getSubjects(T('skos', 'inScheme'), T('lb', schemeLocal), null);
  return concepts
    .map((c) => ({
      iri: c.value,
      label: prefLabel(c),
      ordinal: parseInt(lit(c, T('lb', 'ordinal')) || '0', 10),
    }))
    .sort((a, b) => a.ordinal - b.ordinal);
}

function questionOptions(q) {
  if (q.boolean) {
    return [
      { iri: 'false', label: 'não', ordinal: 0 },
      { iri: 'true', label: 'sim', ordinal: 1 },
    ];
  }
  return schemeOptions(q.scheme);
}

function questionLabel(q) {
  return lit(T('lb', q.prop), T('rdfs', 'label')) || q.prop;
}

function readPlace(place) {
  if (!place) return null;
  const lat = lit(place, T('schema', 'latitude'));
  const lon = lit(place, T('schema', 'longitude'));
  return {
    name: lit(place, T('schema', 'name')),
    lat: lat === null ? null : parseFloat(lat),
    lon: lon === null ? null : parseFloat(lon),
  };
}

function readReview(iri) {
  const rating = obj(iri, T('schema', 'reviewRating'));
  const ratingValue = rating ? lit(rating, T('schema', 'ratingValue')) : null;
  const trip = obj(iri, T('lb', 'trip'));
  const answers = {};
  for (const q of QUESTIONS) {
    const o = obj(iri, T('lb', q.prop));
    if (!o) continue;
    answers[q.prop] = o.value; // IRI do conceito, ou 'true'/'false' se booleana
  }
  const paid = obj(iri, T('lb', 'amountPaid'));
  const paidValue = paid ? lit(paid, T('schema', 'value')) : null;
  return {
    iri,
    amountPaid: paidValue === null ? null : parseFloat(paidValue),
    slug: iri.value.split('/').pop(),
    isLocal: localStore.countQuads(iri, RDF_TYPE, T('lb', 'Review'), null) > 0,
    source: (obj(iri, T('prov', 'wasDerivedFrom')) || {}).value || null,
    answers,
    score: ratingValue === null ? null : parseInt(ratingValue, 10),
    date: trip ? lit(trip, T('lb', 'tripDate')) : null,
    from: trip ? readPlace(obj(trip, T('lb', 'departurePlace'))) : null,
    to: trip ? readPlace(obj(trip, T('lb', 'arrivalPlace'))) : null,
    body: lit(iri, T('schema', 'reviewBody')),
    photos: store.getObjects(iri, T('schema', 'image'), null).map((o) => o.value),
    isExample: lit(iri, T('lb', 'isExample')) === 'true',
    generatedAt: lit(iri, T('prov', 'generatedAtTime')),
  };
}

function allCompanies() {
  return store.getSubjects(RDF_TYPE, T('lb', 'Company'), null).map((iri) => {
    const reviews = store
      .getSubjects(T('schema', 'itemReviewed'), iri, null)
      .filter((r) => store.countQuads(r, RDF_TYPE, T('lb', 'Review'), null) > 0)
      .map(readReview);
    const scores = reviews.map((r) => r.score).filter((s) => s !== null);
    const score = scores.length
      ? scores.reduce((a, b) => a + b, 0) / scores.length
      : null;
    const modeObj = obj(iri, T('lb', 'mode'));
    return {
      iri,
      slug: iri.value.split('/').pop(),
      name: lit(iri, T('schema', 'name')) || iri.value,
      mode: modeObj ? modeObj.value : null,
      reviews,
      score,
    };
  });
}

function rankedCompanies() {
  return allCompanies()
    .filter((c) => c.reviews.length > 0)
    .sort(
      (a, b) =>
        b.score - a.score ||
        b.reviews.length - a.reviews.length ||
        a.name.localeCompare(b.name, 'pt')
    );
}

function scoreBucket(score) {
  return Math.min(5, Math.max(1, Math.round(score)));
}

// ===================== utilidades de interface =====================

function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function fmtScore(score) {
  return score === null ? '—' : score.toFixed(1).replace('.', ',');
}

function scoreChip(score, { max = true } = {}) {
  if (score === null) return '<span class="score-chip">—</span>';
  const color = SCORE_COLORS[scoreBucket(score)];
  return (
    `<span class="score-chip"><span class="score-dot" style="background:${color}"></span>` +
    `${fmtScore(score)}${max ? '<span class="score-max">/5</span>' : ''}</span>`
  );
}

// Datas sempre em ISO (AAAA-MM-DD) — na exibição e no formulário —
// igual ao que o grafo guarda (xsd:date).
function fmtDate(iso) {
  return iso || null;
}

function validDateISO(s) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
  const d = new Date(s + 'T12:00:00');
  return !isNaN(d) && d.toISOString().slice(0, 10) === s;
}

function modeLabel(modeIri) {
  return modeIri ? prefLabel(namedNode(modeIri)) : 'outro';
}

function answerLabel(q, value) {
  if (q.boolean) return value === 'true' ? 'sim' : 'não';
  return prefLabel(namedNode(value));
}

function answerOrdinal(q, value) {
  if (q.boolean) return value === 'true' ? 1 : 0;
  const o = lit(namedNode(value), T('lb', 'ordinal'));
  return o === null ? 0 : parseInt(o, 10);
}

// ===================== telas =====================

const views = {
  ranking: document.getElementById('view-ranking'),
  map: document.getElementById('view-map'),
  news: document.getElementById('view-news'),
  company: document.getElementById('view-company'),
  form: document.getElementById('view-form'),
};

// filtro de modal do ranking — padrão: ônibus ('all' = todos)
let rankingMode = NS.lb + 'modeBus';

function showView(name) {
  for (const [k, el] of Object.entries(views)) el.hidden = k !== name;
  document.querySelectorAll('.tabbar a').forEach((a) => {
    a.classList.toggle('active', a.dataset.route === name);
  });
  window.scrollTo(0, 0);
}

// ---------- ranking ----------

function renderModeFilter() {
  const wrap = document.getElementById('mode-filter');
  const pills = Object.keys(MODE_META)
    .map((iri) => ({ iri, html: `${modeIcon(iri)} ${esc(modeLabel(iri))}` }))
    .concat([{ iri: 'all', html: 'todos' }]);
  wrap.innerHTML = pills
    .map(
      (p) =>
        `<button type="button" data-mode="${esc(p.iri)}"` +
        `${p.iri === rankingMode ? ' class="active"' : ''}>${p.html}</button>`
    )
    .join('');
}

function renderRanking() {
  renderModeFilter();
  const list = document.getElementById('ranking-list');
  const companies = rankedCompanies().filter(
    (c) => rankingMode === 'all' || c.mode === rankingMode
  );
  list.innerHTML = companies
    .map((c, i) => {
      const n = c.reviews.length;
      return (
        `<li><a href="#/empresa/${encodeURIComponent(c.slug)}">` +
        `<span class="rank-pos">${i + 1}</span>` +
        `<span class="rank-mode" title="${esc(modeLabel(c.mode))}">${modeIcon(c.mode)}</span>` +
        `<span class="rank-name">${esc(c.name)}` +
        `<span class="rank-count">${n} ${n === 1 ? 'avaliação' : 'avaliações'}</span></span>` +
        scoreChip(c.score) +
        `</a></li>`
      );
    })
    .join('');
  const empty = document.getElementById('ranking-empty');
  empty.hidden = companies.length > 0;
  empty.textContent =
    rankingMode === 'all'
      ? 'Nenhuma avaliação ainda — seja a primeira pessoa a contar como foi levar a bici!'
      : `Nenhuma avaliação de ${modeLabel(rankingMode)} ainda — seja a primeira pessoa a avaliar!`;
  const hasExamples = companies.some((c) => c.reviews.some((r) => r.isExample));
  document.getElementById('seed-note').hidden = !hasExamples;
}

// ---------- novidades ----------

function renderNews() {
  const list = document.getElementById('news-list');
  const rows = rankedCompanies()
    .flatMap((c) => c.reviews.map((r) => ({ c, r })))
    .sort(
      (a, b) =>
        (b.r.generatedAt || '').localeCompare(a.r.generatedAt || '') ||
        (b.r.date || '').localeCompare(a.r.date || '') ||
        a.c.name.localeCompare(b.c.name, 'pt')
    )
    .slice(0, 30);
  list.innerHTML = rows
    .map(({ c, r }) => {
      const when = (r.generatedAt || '').slice(0, 10) || r.date || '';
      const route =
        r.from || r.to
          ? `${esc((r.from && r.from.name) || '?')} → ${esc((r.to && r.to.name) || '?')}`
          : null;
      const excerpt = r.body
        ? esc(r.body.length > 110 ? r.body.slice(0, 110).trimEnd() + '…' : r.body)
        : null;
      return (
        `<li><a class="news-row" href="#/empresa/${encodeURIComponent(c.slug)}">` +
        `<div class="news-head">${scoreChip(r.score)}` +
        `<span class="news-company">${modeIcon(c.mode)} ${esc(c.name)}</span>` +
        `<span class="news-date">${esc(when)}</span></div>` +
        (route ? `<div class="news-route">${route}</div>` : '') +
        (excerpt ? `<div class="news-excerpt">${excerpt}</div>` : '') +
        `</a></li>`
      );
    })
    .join('');
  document.getElementById('news-empty').hidden = rows.length > 0;
}

// ---------- mapa ----------

// Arco quadrático entre dois pontos, com deflexão alternada por índice
// pra trajetos coincidentes (ida/volta, empresas na mesma linha) não se
// cobrirem por completo.
function arcPoints(from, to, index) {
  const mx = (from.lat + to.lat) / 2;
  const my = (from.lon + to.lon) / 2;
  const dx = to.lat - from.lat;
  const dy = to.lon - from.lon;
  const side = index % 2 === 0 ? 1 : -1;
  const bend = 0.12 * (1 + Math.floor(index / 2) * 0.5) * side;
  const cx = mx - dy * bend;
  const cy = my + dx * bend;
  const pts = [];
  for (let i = 0; i <= 24; i++) {
    const t = i / 24;
    const a = (1 - t) * (1 - t);
    const b = 2 * t * (1 - t);
    const c = t * t;
    pts.push([
      a * from.lat + b * cx + c * to.lat,
      a * from.lon + b * cy + c * to.lon,
    ]);
  }
  return pts;
}

function renderMap() {
  if (!map) {
    map = L.map('map', { zoomControl: true }).setView([-14.2, -51.9], 4);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);

    const legend = L.control({ position: 'bottomright' });
    legend.onAdd = () => {
      const div = L.DomUtil.create('div', 'map-legend');
      div.innerHTML =
        '<div class="legend-title">nota de amigabilidade</div>' +
        [1, 2, 3, 4, 5]
          .map((s) => `<div><span class="legend-swatch" style="background:${SCORE_COLORS[s]}"></span>${s}</div>`)
          .join('') +
        '<div class="legend-note">cada linha é um trajeto avaliado; a cor é a nota média da empresa</div>';
      return div;
    };
    legend.addTo(map);
  }

  if (mapLayer) mapLayer.remove();
  mapLayer = L.layerGroup().addTo(map);

  const bounds = [];
  let arcIndex = 0;
  for (const c of rankedCompanies()) {
    const color = SCORE_COLORS[scoreBucket(c.score)];
    for (const r of c.reviews) {
      if (!r.from || !r.to) continue;
      if ([r.from.lat, r.from.lon, r.to.lat, r.to.lon].some((v) => v === null || isNaN(v))) continue;
      const pts = arcPoints(r.from, r.to, arcIndex++);
      // linha com contorno branco por baixo pra ler sobre o basemap
      L.polyline(pts, { color: '#ffffff', weight: 7, opacity: 0.85, interactive: false }).addTo(mapLayer);
      const line = L.polyline(pts, { color, weight: 4, opacity: 0.95 }).addTo(mapLayer);
      line.bindPopup(
        `<div class="map-popup">` +
        `<div class="popup-company">${modeIcon(c.mode)} ${esc(c.name)} ${scoreChip(c.score)}</div>` +
        `<div class="popup-route">${esc(r.from.name || '?')} → ${esc(r.to.name || '?')}` +
        (r.date ? ` · ${fmtDate(r.date)}` : '') + `</div>` +
        `<a href="#/empresa/${encodeURIComponent(c.slug)}">ver empresa</a>` +
        `</div>`
      );
      bounds.push([r.from.lat, r.from.lon], [r.to.lat, r.to.lon]);
    }
  }
  // o container estava hidden — o Leaflet precisa remedir
  setTimeout(() => {
    map.invalidateSize();
    if (bounds.length) map.fitBounds(bounds, { padding: [30, 30] });
  }, 0);
}

// ---------- empresa ----------

function statRows(reviews) {
  return QUESTIONS.map((q) => {
    const options = questionOptions(q);
    const counts = options.map(
      (o) => reviews.filter((r) => r.answers[q.prop] === o.iri).length
    );
    const total = counts.reduce((a, b) => a + b, 0);
    if (!total) return '';
    const text = options
      .map((o, i) => (counts[i] ? `${esc(o.label)} ×${counts[i]}` : ''))
      .filter(Boolean)
      .join(' · ');
    const bar = options
      .map((o, i) =>
        counts[i]
          ? `<span style="flex:${counts[i]};background:${FRICTION_COLORS[o.ordinal]}"></span>`
          : ''
      )
      .join('');
    return (
      `<div class="stat-row"><span class="stat-label">${esc(questionLabel(q))}</span>` +
      `<span class="stat-text">${text}</span>` +
      `<span class="stat-bar" aria-hidden="true">${bar}</span></div>`
    );
  }).join('');
}

function reviewCard(r, q2) {
  const route =
    r.from || r.to
      ? `${esc((r.from && r.from.name) || '?')} → ${esc((r.to && r.to.name) || '?')}`
      : 'trajeto não informado';
  const badges = QUESTIONS.filter((q) => r.answers[q.prop] !== undefined)
    .filter((q) => answerOrdinal(q, r.answers[q.prop]) > 0)
    .map(
      (q) =>
        `<span class="badge">${esc(q.short)}: ${esc(answerLabel(q, r.answers[q.prop]))}</span>`
    );
  if (r.amountPaid !== null && r.amountPaid > 0)
    badges.push(
      `<span class="badge">pagou R$ ${r.amountPaid.toFixed(2).replace('.', ',')}</span>`
    );
  const answered = QUESTIONS.some((q) => r.answers[q.prop] !== undefined);
  const badgesHtml = badges.length
    ? badges.join('')
    : answered
      ? `<span class="badge">sem atritos ${icon('sparkle')}</span>`
      : '';
  return (
    `<li class="review-card">` +
    `<div class="review-head">${scoreChip(r.score)}` +
    `<span class="review-route">${route}</span>` +
    (r.date ? `<span class="review-date">${esc(fmtDate(r.date))}</span>` : '') +
    (r.isExample ? '<span class="badge badge-example">exemplo</span>' : '') +
    (r.source
      ? `<a class="badge badge-source" href="${esc(r.source)}" target="_blank" rel="noopener">fonte ↗</a>`
      : '') +
    (r.isLocal ? '<span class="badge badge-local">só neste aparelho</span>' : '') +
    `</div>` +
    (badgesHtml ? `<div class="answer-badges">${badgesHtml}</div>` : '') +
    (r.body ? `<p class="review-body">${esc(r.body)}</p>` : '') +
    (r.photos.length
      ? `<div class="review-photos">${r.photos
          .map((p) => `<img src="${esc(p)}" alt="foto da avaliação" loading="lazy">`)
          .join('')}</div>`
      : '') +
    reviewActions(r) +
    `</li>`
  );
}

// Moderação estilo wiki: qualquer pessoa edita/apaga; a proteção é o
// histórico de versões do grafo no servidor.
function reviewActions(r) {
  const btns = [];
  if (r.isLocal && apiAvailable)
    btns.push(
      `<button class="btn-mini" data-action="publish" data-slug="${esc(r.slug)}">⤴ publicar</button>`
    );
  if (r.isLocal || apiAvailable) {
    btns.push(
      `<button class="btn-mini" data-action="edit" data-slug="${esc(r.slug)}">✎ editar</button>`,
      `<button class="btn-mini btn-mini-danger" data-action="delete" data-slug="${esc(r.slug)}">${icon('trash')} apagar</button>`
    );
  }
  return btns.length ? `<div class="review-actions">${btns.join('')}</div>` : '';
}

async function handleReviewAction(action, slug) {
  if (action === 'edit') {
    location.hash = '#/editar/' + encodeURIComponent(slug);
    return;
  }
  const reviewIri = namedNode(NS.av + slug);
  const isLocal = localStore.countQuads(reviewIri, RDF_TYPE, T('lb', 'Review'), null) > 0;
  try {
    if (action === 'delete') {
      const msg = isLocal
        ? 'Apagar esta avaliação (ainda não publicada)?'
        : 'Apagar esta avaliação do grafo compartilhado? O histórico de versões guarda a recuperação.';
      if (!confirm(msg)) return;
      if (isLocal) {
        localStore.removeQuads(reviewSubtreeQuads(localStore, reviewIri));
        await persistLocal();
        rebuildStore();
      } else {
        await apiFetch('api/reviews/' + encodeURIComponent(slug), { method: 'DELETE' });
        await refreshGraph();
      }
    } else if (action === 'publish') {
      // subárvore local + a empresa, caso ela também tenha nascido aqui
      const quads = reviewSubtreeQuads(localStore, reviewIri);
      const companyQuad = quads.find((q) => q.predicate.value === NS.schema + 'itemReviewed');
      if (companyQuad)
        quads.push(...localStore.getQuads(namedNode(companyQuad.object.value), null, null, null));
      await apiFetch('api/reviews', {
        method: 'POST',
        headers: { 'Content-Type': 'text/turtle' },
        body: await serializeQuads(quads),
      });
      localStore.removeQuads(reviewSubtreeQuads(localStore, reviewIri));
      await persistLocal();
      await refreshGraph();
    }
    route();
  } catch (e) {
    alert('Não deu: ' + e.message);
  }
}

function renderCompany(slug) {
  const company = allCompanies().find((c) => c.slug === slug);
  const card = document.getElementById('company-card');
  const list = document.getElementById('company-reviews');
  if (!company) {
    card.innerHTML = '<div class="company-card">Empresa não encontrada.</div>';
    list.innerHTML = '';
    return;
  }
  const n = company.reviews.length;
  card.innerHTML =
    `<div class="company-card">` +
    `<div class="company-head"><h2>${esc(company.name)}</h2>` +
    `<span class="mode-tag">${modeIcon(company.mode)} ${esc(modeLabel(company.mode))}</span></div>` +
    `<div class="company-hero">` +
    `<span class="score-dot" style="background:${
      company.score === null ? 'var(--hairline)' : SCORE_COLORS[scoreBucket(company.score)]
    }"></span>` +
    `<span class="hero-score">${fmtScore(company.score)}<span class="score-max">/5</span></span>` +
    `<span class="hero-sub">amigabilidade à bici<br>${n} ${n === 1 ? 'avaliação' : 'avaliações'}</span>` +
    `</div>` +
    statRows(company.reviews) +
    `</div>`;
  const sorted = [...company.reviews].sort((a, b) =>
    (b.date || b.generatedAt || '').localeCompare(a.date || a.generatedAt || '')
  );
  list.innerHTML = sorted.map(reviewCard).join('');
}

// ---------- formulário ----------

function renderForm(editSlug = null) {
  editingSlug = editSlug;
  const select = document.getElementById('f-company');
  // mais avaliadas primeiro (pedido do coletivo); nome desempata
  const companies = allCompanies().sort(
    (a, b) => b.reviews.length - a.reviews.length || a.name.localeCompare(b.name, 'pt')
  );

  // Tom Select (vendorado do amora): busca tolerante a acentos e preview
  // da nota de cada empresa direto no dropdown; digitar um nome novo
  // oferece “criar empresa”.
  if (companySelect) {
    companySelect.destroy();
    companySelect = null;
  }
  select.innerHTML = '<option value=""></option>';
  companySelect = new TomSelect(select, {
    options: [
      ...companies.map((c) => ({
        value: c.slug,
        name: c.name,
        emoji: modeIcon(c.mode),
        score: c.score,
        count: c.reviews.length,
      })),
      { value: '__new__', name: 'outra empresa…', emoji: '＋', score: null, count: 0 },
    ],
    valueField: 'value',
    searchField: ['name'],
    // relevância da busca primeiro; sem busca, mais avaliadas primeiro
    sortField: [
      { field: '$score' },
      { field: 'count', direction: 'desc' },
      { field: 'name' },
    ],
    maxItems: 1,
    placeholder: '— escolha ou digite pra buscar —',
    create: (input) => {
      document.getElementById('f-company-new').value = input.trim();
      return { value: '__new__', name: input.trim(), emoji: '＋', score: null, count: 0 };
    },
    createFilter: (input) => input.trim().length > 1,
    render: {
      option: (data, escape) => {
        const chip = data.count
          ? scoreChip(data.score) +
            `<span class="ts-count">${data.count} ${data.count === 1 ? 'aval.' : 'avals.'}</span>`
          : '<span class="ts-count">sem avaliações</span>';
        return (
          `<div class="ts-company"><span>${data.emoji} ${escape(data.name)}</span>` +
          `<span class="ts-chip">${chip}</span></div>`
        );
      },
      item: (data, escape) => `<div>${data.emoji} ${escape(data.name)}</div>`,
      option_create: (data, escape) =>
        `<div class="create">＋ criar empresa “${escape(data.input)}”</div>`,
      no_results: () => '<div class="no-results">nenhuma empresa encontrada</div>',
    },
    onChange: (value) => {
      document.getElementById('new-company-fields').hidden = value !== '__new__';
    },
  });

  const modeSelect = document.getElementById('f-company-mode');
  modeSelect.innerHTML = schemeOptions('TransportModeScheme')
    .map((m) => `<option value="${esc(m.iri)}">${esc(m.label)}</option>`)
    .join('');

  const scorePicker = document.getElementById('f-score');
  scorePicker.innerHTML = [1, 2, 3, 4, 5]
    .map(
      (s) =>
        `<label><input type="radio" name="score" value="${s}">` +
        `<span class="score-dot" style="background:${SCORE_COLORS[s]}"></span>${s}</label>`
    )
    .join('');

  const groups = document.getElementById('question-groups');
  groups.innerHTML = QUESTIONS.map((q) => {
    const radios = questionOptions(q)
      .map(
        (o) =>
          `<label><input type="radio" name="${q.prop}" value="${esc(o.iri)}">${esc(o.label)}</label>`
      )
      .join('');
    // “!” = recomendado (Warning nas shapes), como o “*” = obrigatório
    return (
      `<label id="ql-${q.prop}">${esc(questionLabel(q))} <span class="rec">!</span></label>` +
      `<div class="radio-group" role="radiogroup" aria-labelledby="ql-${q.prop}">${radios}</div>`
    );
  }).join('');

  pendingPhotos = [];
  renderPhotoPreviews();
  warningsConfirmed = false;
  const box = document.getElementById('form-validation');
  box.hidden = true;
  document.getElementById('review-form').reset();
  document.getElementById('new-company-fields').hidden = true;
  document.getElementById('form-title').textContent = editSlug
    ? 'Editar avaliação'
    : 'Nova avaliação';
  document.getElementById('btn-submit').textContent = editSlug
    ? 'salvar edição'
    : 'salvar avaliação';
  if (editSlug) prefillForm(editSlug);
}

function checkRadio(name, value) {
  const input = [...document.querySelectorAll(`#review-form input[name="${name}"]`)].find(
    (i) => i.value === value
  );
  if (input) {
    input.checked = true;
    input.closest('label').classList.add('checked');
  }
}

function prefillForm(slug) {
  const reviewIri = namedNode(NS.av + slug);
  const r = readReview(reviewIri);
  const companyIri = obj(reviewIri, T('schema', 'itemReviewed'));
  if (companyIri) {
    const companySlug = companyIri.value.split('/').pop();
    if (companySelect) companySelect.setValue(companySlug, true);
    document.getElementById('f-company').value = companySlug;
  }
  if (r.score !== null) checkRadio('score', String(r.score));
  if (r.date) document.getElementById('f-date').value = r.date;
  if (r.from && r.from.name) document.getElementById('f-from').value = r.from.name;
  if (r.to && r.to.name) document.getElementById('f-to').value = r.to.name;
  if (r.body) document.getElementById('f-comment').value = r.body;
  if (r.amountPaid !== null)
    document.getElementById('f-amount').value = r.amountPaid.toFixed(2).replace('.', ',');
  for (const q of QUESTIONS) {
    if (r.answers[q.prop] !== undefined) checkRadio(q.prop, r.answers[q.prop]);
  }
  pendingPhotos = [...r.photos];
  renderPhotoPreviews();
}

function renderPhotoPreviews() {
  document.getElementById('photo-previews').innerHTML = pendingPhotos
    .map(
      (p, i) =>
        `<span class="thumb"><img src="${esc(p)}" alt="foto ${i + 1}">` +
        `<button type="button" data-remove="${i}" aria-label="remover foto ${i + 1}">×</button></span>`
    )
    .join('');
}

// Reduz a foto pra caber no localStorage (~5 MB no total).
function shrinkPhoto(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const maxSide = 1024;
      const scale = Math.min(1, maxSide / Math.max(img.width, img.height));
      const canvas = document.createElement('canvas');
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      resolve(canvas.toDataURL('image/jpeg', 0.75));
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('imagem ilegível'));
    };
    img.src = url;
  });
}

function readFormState() {
  const form = document.getElementById('review-form');
  const companySlug = document.getElementById('f-company').value;
  const answers = {};
  for (const q of QUESTIONS) {
    const checked = form.querySelector(`input[name="${q.prop}"]:checked`);
    if (checked) answers[q.prop] = checked.value;
  }
  const scoreInput = form.querySelector('input[name="score"]:checked');
  return {
    companySlug,
    newCompanyName: document.getElementById('f-company-new').value.trim(),
    newCompanyMode: document.getElementById('f-company-mode').value,
    score: scoreInput ? parseInt(scoreInput.value, 10) : null,
    date: document.getElementById('f-date').value.trim() || null,
    from: document.getElementById('f-from').value.trim() || null,
    to: document.getElementById('f-to').value.trim() || null,
    comment: document.getElementById('f-comment').value.trim() || null,
    amountRaw: document.getElementById('f-amount').value.trim(),
    answers,
  };
}

// "27,00" / "R$ 27" → 27.0 ; vazio → null ; ilegível → NaN
function parseAmount(raw) {
  if (!raw) return null;
  const n = parseFloat(raw.replace(/\s|R\$/gi, '').replace(',', '.'));
  return isNaN(n) || n < 0 ? NaN : n;
}

// Espelha data/shapes.ttl: Violation bloqueia, Warning só avisa.
function validateForm(s) {
  const violations = [];
  const warnings = [];
  if (!s.companySlug) violations.push('escolha a empresa');
  if (s.companySlug === '__new__' && !s.newCompanyName)
    violations.push('dê um nome à nova empresa');
  if (s.score === null) violations.push('dê a nota de amigabilidade (1–5)');
  // presença da data é Warning; data PRESENTE e malformada é Violation
  // (espelha o par de shapes de lb:tripDate)
  if (s.date && !validDateISO(s.date))
    violations.push('data da viagem no formato AAAA-MM-DD (ex.: 2026-08-14)');
  if (s.amountRaw && isNaN(parseAmount(s.amountRaw)))
    violations.push('valor pago em número (ex.: 27,00)');
  if (!s.date) warnings.push('quando foi a viagem');
  for (const q of QUESTIONS) {
    if (s.answers[q.prop] === undefined) warnings.push(questionLabel(q).toLowerCase());
  }
  return { violations, warnings };
}

function showValidation({ violations, warnings }) {
  const box = document.getElementById('form-validation');
  if (!violations.length && !warnings.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.className =
    'form-validation ' + (violations.length ? 'has-violations' : 'warnings-only');
  let html = '';
  if (violations.length) {
    html +=
      '<strong>Faltam campos obrigatórios:</strong><ul>' +
      violations.map((v) => `<li class="v-violation">${esc(v)}</li>`).join('') +
      '</ul>';
  }
  if (warnings.length) {
    html +=
      '<strong>Ideal preencher também:</strong><ul>' +
      warnings.map((w) => `<li>${esc(w)}</li>`).join('') +
      '</ul>';
    if (!violations.length)
      html += 'Pode salvar assim mesmo — toque de novo em <em>salvar</em>.';
  }
  box.innerHTML = html;
  box.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function slugify(name) {
  return name
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

// Geocodificação opcional (Nominatim, melhor esforço): sem resultado ou
// offline, a avaliação só não entra no mapa.
async function geocode(name) {
  const url =
    'https://nominatim.openstreetmap.org/search?format=json&limit=1&countrycodes=br&q=' +
    encodeURIComponent(name);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 5000);
  try {
    const res = await fetch(url, { signal: ctrl.signal });
    const results = await res.json();
    if (results.length)
      return { lat: parseFloat(results[0].lat), lon: parseFloat(results[0].lon) };
  } catch (e) {
    /* melhor esforço */
  } finally {
    clearTimeout(timer);
  }
  return null;
}

function xsdLiteral(value, type) {
  return literal(String(value), T('xsd', type));
}

// ---------- calendário próprio do campo "Quando" ----------
// Grade de dias por padrão; tocar no título mês/ano abre a seleção de
// mês+ano; tocar num mês OU no espaço vazio volta pra grade de dias;
// tocar fora fecha. Escreve sempre ISO (AAAA-MM-DD) no campo de texto.

const MONTHS_PT = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
  'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro'];
const cal = { y: 0, m: 0, mode: 'days' }; // mode: 'days' | 'monthyear'

function pad2(n) {
  return String(n).padStart(2, '0');
}

function renderCalendar() {
  const pop = document.getElementById('cal-pop');
  const selected = document.getElementById('f-date').value.trim();
  const todayIso = new Date().toISOString().slice(0, 10);

  if (cal.mode === 'monthyear') {
    pop.innerHTML =
      `<div class="cal-head">` +
      `<button type="button" data-cal="yprev" aria-label="Ano anterior">‹</button>` +
      `<span class="cal-title">${cal.y}</span>` +
      `<button type="button" data-cal="ynext" aria-label="Ano seguinte">›</button>` +
      `</div>` +
      `<div class="cal-months">` +
      MONTHS_PT.map(
        (name, i) =>
          `<button type="button" data-month="${i}"` +
          `${i === cal.m ? ' class="cal-now"' : ''}>${name.slice(0, 3)}</button>`
      ).join('') +
      `</div>` +
      `<div class="cal-hint">toque num mês (ou no espaço vazio) pra voltar aos dias</div>`;
    return;
  }

  const firstDow = new Date(cal.y, cal.m, 1).getDay(); // 0 = domingo
  const daysInMonth = new Date(cal.y, cal.m + 1, 0).getDate();
  let cells = '<span></span>'.repeat(firstDow);
  for (let d = 1; d <= daysInMonth; d++) {
    const iso = `${cal.y}-${pad2(cal.m + 1)}-${pad2(d)}`;
    const cls =
      (iso === selected ? 'cal-selected ' : '') + (iso === todayIso ? 'cal-now' : '');
    cells += `<button type="button" data-day="${iso}"${cls ? ` class="${cls.trim()}"` : ''}>${d}</button>`;
  }
  pop.innerHTML =
    `<div class="cal-head">` +
    `<button type="button" data-cal="prev" aria-label="Mês anterior">‹</button>` +
    `<button type="button" data-cal="title" class="cal-title">${MONTHS_PT[cal.m]} ${cal.y}</button>` +
    `<button type="button" data-cal="next" aria-label="Mês seguinte">›</button>` +
    `</div>` +
    `<div class="cal-grid cal-week"><span>D</span><span>S</span><span>T</span><span>Q</span><span>Q</span><span>S</span><span>S</span></div>` +
    `<div class="cal-grid">${cells}</div>`;
}

function toggleCalendar(show) {
  const pop = document.getElementById('cal-pop');
  pop.hidden = !show;
  document.getElementById('btn-date-picker').setAttribute('aria-expanded', String(show));
  if (show) {
    const current = document.getElementById('f-date').value.trim();
    const base = validDateISO(current) ? new Date(current + 'T12:00:00') : new Date();
    cal.y = base.getFullYear();
    cal.m = base.getMonth();
    cal.mode = 'days';
    renderCalendar();
  }
}

function initCalendar() {
  const pop = document.getElementById('cal-pop');
  document.getElementById('btn-date-picker').addEventListener('click', () => {
    toggleCalendar(pop.hidden);
  });

  pop.addEventListener('click', (e) => {
    // não deixa subir pro fechador de "clique fora": o re-render abaixo
    // desanexa o alvo do DOM e o closest() lá em cima erraria
    e.stopPropagation();
    const day = e.target.closest('[data-day]');
    if (day) {
      document.getElementById('f-date').value = day.dataset.day;
      toggleCalendar(false);
      return;
    }
    const month = e.target.closest('[data-month]');
    if (month) {
      cal.m = parseInt(month.dataset.month, 10);
      cal.mode = 'days';
      renderCalendar();
      return;
    }
    const nav = e.target.closest('[data-cal]');
    if (nav) {
      const op = nav.dataset.cal;
      if (op === 'prev' || op === 'next') {
        cal.m += op === 'next' ? 1 : -1;
        if (cal.m < 0) { cal.m = 11; cal.y--; }
        if (cal.m > 11) { cal.m = 0; cal.y++; }
      } else if (op === 'yprev' || op === 'ynext') {
        cal.y += op === 'ynext' ? 1 : -1;
      } else if (op === 'title') {
        cal.mode = 'monthyear';
      }
      renderCalendar();
      return;
    }
    // espaço vazio do popover: da seleção de mês/ano volta pros dias
    if (cal.mode === 'monthyear') {
      cal.mode = 'days';
      renderCalendar();
    }
  });

  // toque fora fecha
  document.addEventListener('click', (e) => {
    if (pop.hidden) return;
    if (!e.target.closest('.date-row')) toggleCalendar(false);
  });
}

async function saveQuadsLocally(quads) {
  localStore.addQuads(quads);
  try {
    await persistLocal();
  } catch (e) {
    localStore.removeQuads(quads);
    alert('Não coube no armazenamento local do navegador — tente com menos fotos.');
    return false;
  }
  rebuildStore();
  return true;
}

async function submitReview(event) {
  event.preventDefault();
  const state = readFormState();
  const result = validateForm(state);
  if (result.violations.length) {
    showValidation(result);
    warningsConfirmed = false;
    return;
  }
  if (result.warnings.length && !warningsConfirmed) {
    showValidation(result);
    warningsConfirmed = true;
    document.getElementById('btn-submit').textContent = 'salvar mesmo assim';
    return;
  }

  const btn = document.getElementById('btn-submit');
  btn.disabled = true;
  btn.textContent = 'salvando…';

  try {
    const quads = [];
    const today = new Date();
    const stamp = today.toISOString();

    // empresa: existente ou nova (slug determinístico; se colidir, reusa)
    let companyIri;
    let companyMode;
    if (state.companySlug === '__new__') {
      const slug = slugify(state.newCompanyName) || 'empresa-' + Date.now();
      companyIri = namedNode(NS.emp + slug);
      const exists = store.countQuads(companyIri, RDF_TYPE, T('lb', 'Company'), null) > 0;
      companyMode = exists
        ? (obj(companyIri, T('lb', 'mode')) || {}).value
        : state.newCompanyMode;
      if (!exists) {
        quads.push(
          quad(companyIri, RDF_TYPE, T('lb', 'Company')),
          quad(companyIri, T('schema', 'name'), literal(state.newCompanyName)),
          quad(companyIri, T('lb', 'mode'), namedNode(state.newCompanyMode))
        );
      }
    } else {
      companyIri = namedNode(NS.emp + state.companySlug);
      companyMode = (obj(companyIri, T('lb', 'mode')) || {}).value;
    }

    // IRIs determinísticos (convenção <pai>_sufixo do ecossistema); na
    // edição o IRI é preservado — os filhos idem, então a subárvore
    // antiga sai e a nova entra sem sobras
    const reviewIri = editingSlug
      ? namedNode(NS.av + editingSlug)
      : namedNode(
          NS.av + stamp.slice(0, 10) + '-' + Math.random().toString(36).slice(2, 8)
        );
    const child = (suffix) => namedNode(reviewIri.value + suffix);

    // proveniência sobrevive à edição (criação original + fonte wiki)
    const generatedAt =
      (editingSlug && lit(reviewIri, T('prov', 'generatedAtTime'))) || stamp;
    const derivedFrom = editingSlug ? obj(reviewIri, T('prov', 'wasDerivedFrom')) : null;

    quads.push(
      quad(reviewIri, RDF_TYPE, T('lb', 'Review')),
      quad(reviewIri, T('schema', 'itemReviewed'), companyIri),
      quad(reviewIri, T('prov', 'generatedAtTime'), xsdLiteral(generatedAt, 'dateTime'))
    );
    if (derivedFrom)
      quads.push(quad(reviewIri, T('prov', 'wasDerivedFrom'), derivedFrom));

    const ratingIri = child('_rating');
    quads.push(
      quad(reviewIri, T('schema', 'reviewRating'), ratingIri),
      quad(ratingIri, RDF_TYPE, T('schema', 'Rating')),
      quad(ratingIri, T('schema', 'ratingValue'), xsdLiteral(state.score, 'integer')),
      quad(ratingIri, T('schema', 'bestRating'), xsdLiteral(5, 'integer')),
      quad(ratingIri, T('schema', 'worstRating'), xsdLiteral(1, 'integer'))
    );

    if (state.date || state.from || state.to) {
      const tripIri = child('_trip');
      const tripClass = (MODE_META[companyMode] || MODE_META[NS.lb + 'modeOther']).tripClass;
      quads.push(
        quad(reviewIri, T('lb', 'trip'), tripIri),
        quad(tripIri, RDF_TYPE, namedNode(tripClass))
      );
      if (state.date)
        quads.push(quad(tripIri, T('lb', 'tripDate'), xsdLiteral(state.date, 'date')));
      for (const [key, prop] of [['from', 'departurePlace'], ['to', 'arrivalPlace']]) {
        const name = state[key];
        if (!name) continue;
        const placeIri = child('_trip_' + key);
        quads.push(
          quad(tripIri, T('lb', prop), placeIri),
          quad(placeIri, RDF_TYPE, T('schema', 'Place')),
          quad(placeIri, T('schema', 'name'), literal(name))
        );
        const coords = await geocode(name);
        if (coords) {
          quads.push(
            quad(placeIri, T('schema', 'latitude'), xsdLiteral(coords.lat, 'decimal')),
            quad(placeIri, T('schema', 'longitude'), xsdLiteral(coords.lon, 'decimal'))
          );
        }
      }
    }

    for (const q of QUESTIONS) {
      const v = state.answers[q.prop];
      if (v === undefined) continue;
      quads.push(
        quad(
          reviewIri,
          T('lb', q.prop),
          q.boolean ? xsdLiteral(v, 'boolean') : namedNode(v)
        )
      );
    }

    const amount = parseAmount(state.amountRaw);
    if (amount !== null && !isNaN(amount)) {
      const paidIri = child('_paid');
      quads.push(
        quad(reviewIri, T('lb', 'amountPaid'), paidIri),
        quad(paidIri, RDF_TYPE, T('schema', 'MonetaryAmount')),
        quad(paidIri, T('schema', 'value'), xsdLiteral(amount, 'decimal')),
        quad(paidIri, T('schema', 'currency'), literal('BRL'))
      );
    }

    if (state.comment)
      quads.push(quad(reviewIri, T('schema', 'reviewBody'), literal(state.comment)));
    for (const photo of pendingPhotos)
      quads.push(quad(reviewIri, T('schema', 'image'), namedNode(photo)));

    // destino: grafo compartilhado (API) ou só este aparelho (offline)
    const wasLocal = editingSlug
      ? localStore.countQuads(reviewIri, RDF_TYPE, T('lb', 'Review'), null) > 0
      : false;

    if (apiAvailable && !wasLocal) {
      const ttl = await serializeQuads(quads);
      try {
        await apiFetch(
          editingSlug ? 'api/reviews/' + encodeURIComponent(editingSlug) : 'api/reviews',
          {
            method: editingSlug ? 'PUT' : 'POST',
            headers: { 'Content-Type': 'text/turtle' },
            body: ttl,
          }
        );
        await refreshGraph();
      } catch (e) {
        if (editingSlug) {
          alert('Não consegui salvar a edição: ' + e.message);
          return;
        }
        if (!(await saveQuadsLocally(quads))) return;
        alert(
          'Sem conexão com o grafo compartilhado — avaliação guardada só neste ' +
            'aparelho. Use “publicar” quando estiver online.'
        );
      }
    } else if (wasLocal) {
      // edição de avaliação ainda-não-publicada: troca a subárvore local
      const old = reviewSubtreeQuads(localStore, reviewIri);
      localStore.removeQuads(old);
      if (!(await saveQuadsLocally(quads))) {
        localStore.addQuads(old); // restaura a versão anterior
        rebuildStore();
        return;
      }
    } else {
      if (!(await saveQuadsLocally(quads))) return;
    }

    const companySlug = companyIri.value.split('/').pop();
    location.hash = '#/empresa/' + encodeURIComponent(companySlug);
  } finally {
    btn.disabled = false;
    btn.textContent = 'salvar avaliação';
  }
}

// ---------- exportar / apagar ----------

async function exportTurtle() {
  // só instâncias (empresas + avaliações); o vocabulário mora em vocab.ttl
  const quads = store
    .getQuads(null, null, null, null)
    .filter((q) => !q.subject.value.startsWith(NS.lb));
  const ttl = await serializeQuads(quads);
  const blob = new Blob([ttl], { type: 'text/turtle' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'levabici-' + new Date().toISOString().slice(0, 10) + '.ttl';
  a.click();
  URL.revokeObjectURL(a.href);
}


// ===================== roteador =====================

function route() {
  const h = location.hash || '#/';
  const companyMatch = h.match(/^#\/empresa\/(.+)$/);
  const editMatch = h.match(/^#\/editar\/(.+)$/);
  if (h === '#/mapa') {
    showView('map');
    renderMap();
  } else if (h === '#/novidades') {
    renderNews();
    showView('news');
  } else if (h === '#/nova') {
    renderForm(null);
    showView('form');
  } else if (editMatch) {
    renderForm(decodeURIComponent(editMatch[1]));
    showView('form');
  } else if (companyMatch) {
    renderCompany(decodeURIComponent(companyMatch[1]));
    showView('company');
  } else {
    renderRanking();
    showView('ranking');
  }
}

// ===================== inicialização =====================

async function init() {
  // Servido pelo backend, data/reviews.ttl é o grafo VIVO do bucket;
  // no GitHub Pages/offline é a semente estática — e sem api/health o
  // app degrada pra modo só-local (escreve no aparelho, publica depois).
  const [vocabTtl, graphTtl, healthy] = await Promise.all([
    fetch('data/vocab.ttl').then((r) => r.text()),
    fetch('data/reviews.ttl').then((r) => r.text()),
    fetch('api/health', { signal: AbortSignal.timeout(4000) })
      .then((r) => r.ok)
      .catch(() => false),
  ]);
  apiAvailable = healthy;
  vocabQuads = parseTurtle(vocabTtl);
  serverText = graphTtl;

  const localTtl = localStorage.getItem(LOCAL_KEY);
  if (localTtl) {
    try {
      localStore.addQuads(parseTurtle(localTtl));
    } catch (e) {
      console.error('grafo local ilegível — ignorado', e);
    }
  }
  rebuildStore();

  // ---- eventos ----
  window.addEventListener('hashchange', route);

  document.getElementById('f-company').addEventListener('change', (e) => {
    document.getElementById('new-company-fields').hidden = e.target.value !== '__new__';
  });

  initCalendar();

  // rádios estilizados: classe .checked acompanha o input marcado
  document.getElementById('review-form').addEventListener('change', (e) => {
    if (e.target.type !== 'radio') return;
    document
      .querySelectorAll(`input[name="${e.target.name}"]`)
      .forEach((input) => input.closest('label').classList.toggle('checked', input.checked));
  });

  document.getElementById('f-photos').addEventListener('change', async (e) => {
    for (const file of e.target.files) {
      try {
        pendingPhotos.push(await shrinkPhoto(file));
      } catch (err) {
        console.error(err);
      }
    }
    e.target.value = '';
    renderPhotoPreviews();
  });

  document.getElementById('photo-previews').addEventListener('click', (e) => {
    const idx = e.target.dataset && e.target.dataset.remove;
    if (idx !== undefined) {
      pendingPhotos.splice(parseInt(idx, 10), 1);
      renderPhotoPreviews();
    }
  });

  document.getElementById('company-reviews').addEventListener('click', (e) => {
    if (e.target.tagName === 'IMG') {
      e.target.classList.toggle('zoomed');
      return;
    }
    const btn = e.target.closest('button[data-action]');
    if (btn) handleReviewAction(btn.dataset.action, btn.dataset.slug);
  });

  document.getElementById('mode-filter').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-mode]');
    if (!btn) return;
    rankingMode = btn.dataset.mode;
    renderRanking();
  });

  document.getElementById('review-form').addEventListener('submit', submitReview);
  document.getElementById('btn-export').addEventListener('click', exportTurtle);

  route();

  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js');
}

init().catch((e) => {
  console.error(e && e.stack ? e.stack : e);
  document.getElementById('main').insertAdjacentHTML(
    'afterbegin',
    '<div class="note" style="margin:16px">⚠️ Não consegui carregar o grafo de dados. ' +
      'Verifique a conexão e recarregue.</div>'
  );
});
