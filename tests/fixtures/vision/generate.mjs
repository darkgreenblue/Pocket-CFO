#!/usr/bin/env node
// Generates synthetic Persian test images (PNG) for the vision/photo-input feature
// plus their ground truth (expected.json).
//
// Single source of truth: every image is rendered FROM the data objects below, and
// expected.json is written from the very same objects (totals are computed in code
// and asserted), so the pictures and the ground truth can never disagree.
//
// Usage (from the repo root):
//   node tests/fixtures/vision/generate.mjs
//
// Requirements: Node 18+, the `playwright` package (local or global install) and its
// Chromium build. Fonts (Vazirmatn, Roboto Mono) come from Google Fonts; font requests
// are fetched on the Node side (route.fetch) so they also work behind a TLS-inspecting
// proxy whose CA Node trusts (NODE_EXTRA_CA_CERTS) but Chromium does not.

import { execSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { writeFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const OUT_DIR = path.dirname(fileURLToPath(import.meta.url));

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

const FA_DIGITS = '۰۱۲۳۴۵۶۷۸۹';
/** Latin digits -> Persian digits (also '.' -> Persian decimal separator, ',' -> Persian thousands separator). */
const fa = (s) =>
  String(s)
    .replace(/\d/g, (d) => FA_DIGITS[d])
    .replace(/,/g, '٬')
    .replace(/\./g, '٫');
/** 1500000 -> "1,500,000" */
const money = (n) => n.toLocaleString('en-US');
/** 1500000 -> "۱٬۵۰۰٬۰۰۰" */
const faMoney = (n) => fa(money(n));
const sum = (arr) => arr.reduce((a, b) => a + b, 0);

function assert(cond, msg) {
  if (!cond) throw new Error('Ground-truth assertion failed: ' + msg);
}

const FONT_CSS_URL =
  'https://fonts.googleapis.com/css2?family=Roboto+Mono:wght@400;500;700&family=Vazirmatn:wght@400;500;700;800&display=swap';

function page(css, body) {
  return `<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<link rel="stylesheet" href="${FONT_CSS_URL}">
<style>
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{font-family:'Vazirmatn',sans-serif;-webkit-font-smoothing:antialiased;text-rendering:geometricPrecision}
.ltr{direction:ltr;unicode-bidi:isolate}
${css}
</style>
</head>
<body>${body}</body>
</html>`;
}

// ---------------------------------------------------------------------------
// 1. Supermarket receipt — 15 rows, Latin digits, amounts in rial
// ---------------------------------------------------------------------------

const supermarket = (() => {
  const rows = [
    { name: 'شیر کم‌چرب ۱ لیتری', quantity: 2, unit_price: 750_000 },
    { name: 'نان بربری', quantity: 3, unit_price: 150_000 },
    { name: 'برنج ایرانی طارم ۱ کیلویی', quantity: 1, unit_price: 1_800_000 },
    { name: 'مایع ظرفشویی ۹۰۰ گرمی', quantity: 1, unit_price: 900_000 },
    { name: 'ماست پرچرب ۹۰۰ گرمی', quantity: 1, unit_price: 950_000 },
    { name: 'تخم‌مرغ بسته ۱۰ عددی', quantity: 1, unit_price: 2_400_000 },
    { name: 'گوجه‌فرنگی (کیلو)', quantity: 0.75, unit_price: 485_000 },
    { name: 'پنیر سفید یواف ۴۰۰ گرمی', quantity: 1, unit_price: 1_350_000 },
    { name: 'روغن آفتابگردان ۸۱۰ گرمی', quantity: 1, unit_price: 1_650_000 },
    { name: 'ماکارونی ۵۰۰ گرمی', quantity: 2, unit_price: 620_000 },
    { name: 'چای خشک ۴۵۰ گرمی', quantity: 1, unit_price: 3_900_000 },
    { name: 'شکر سفید ۹۰۰ گرمی', quantity: 1, unit_price: 780_000 },
    { name: 'دستمال کاغذی ۱۰۰ برگ', quantity: 3, unit_price: 420_000 },
    { name: 'رب گوجه‌فرنگی ۸۰۰ گرمی', quantity: 1, unit_price: 1_150_000 },
    { name: 'پودر لباسشویی ۵۰۰ گرمی', quantity: 1, unit_price: 1_450_000 },
  ];
  const items = rows.map((r) => ({ ...r, line_total: Math.round(r.quantity * r.unit_price) }));
  const subtotal = sum(items.map((i) => i.line_total));
  const discount = 500_000;
  const taxBase = subtotal - discount;
  assert(taxBase % 10 === 0, 'supermarket tax base must be divisible by 10 so 10% VAT is an exact integer');
  const tax = taxBase / 10;
  const payable = subtotal - discount + tax;

  assert(items.length === 15, 'supermarket must have exactly 15 rows');
  for (const i of items) {
    assert(Number.isInteger(i.line_total), `line_total integer for ${i.name}`);
    assert(i.line_total === Math.round(i.quantity * i.unit_price), `line_total = qty*price for ${i.name}`);
  }
  assert(items.some((i) => !Number.isInteger(i.quantity)), 'one fractional quantity row');
  assert(payable === subtotal - discount + tax, 'payable formula');

  return {
    file: 'receipt_supermarket_15.png',
    store: 'فروشگاه زنجیره‌ای نمونه',
    date: '1405/07/09',
    time: '18:24',
    items,
    subtotal,
    discount,
    tax,
    payable,
  };
})();

function supermarketHtml(d) {
  const qtyStr = (q) => (Number.isInteger(q) ? String(q) : q.toFixed(2));
  const rows = d.items
    .map(
      (it, idx) => `<tr>
  <td class="c">${idx + 1}</td>
  <td class="name">${it.name}</td>
  <td class="num c">${qtyStr(it.quantity)}</td>
  <td class="num">${money(it.unit_price)}</td>
  <td class="num">${money(it.line_total)}</td>
</tr>`
    )
    .join('\n');

  const css = `
body{background:#d8d5ce;padding:30px 0 34px}
.paper{width:600px;margin:0 auto;background:#fff;color:#111;padding:26px 24px 30px;position:relative;
  box-shadow:0 3px 14px rgba(0,0,0,.22)}
.paper::after{content:"";position:absolute;left:0;right:0;bottom:-10px;height:10px;
  background:linear-gradient(-45deg,transparent 7px,#fff 0) 0 0/14px 10px repeat-x,
             linear-gradient(45deg,transparent 7px,#fff 0) 0 0/14px 10px repeat-x}
.center{text-align:center}
.logo{font-size:25px;font-weight:800;letter-spacing:0}
.sub{font-size:13.5px;color:#222;line-height:1.8}
.dash{border-top:2px dashed #222;margin:12px 0}
.meta{display:flex;justify-content:space-between;font-size:15px;line-height:1.9}
table{width:100%;border-collapse:collapse;font-size:14.5px;table-layout:fixed}
th{font-weight:700;font-size:13.5px;padding:6px 3px;border-bottom:1.5px dashed #222;text-align:right;white-space:nowrap}
td{padding:5px 3px;vertical-align:top;line-height:1.55}
tr:nth-child(even) td{background:#f4f4f4}
td.name{font-weight:500}
.num{font-family:'Roboto Mono','Vazirmatn',monospace;font-size:14.5px;text-align:left;direction:ltr;white-space:nowrap}
.c{text-align:center}
th.c{text-align:center}
th.l{text-align:left}
.totals{width:100%;font-size:16px}
.totals .t{display:flex;justify-content:space-between;align-items:baseline;padding:4px 0}
.totals .v{font-family:'Roboto Mono','Vazirmatn',monospace;direction:ltr;font-size:16px}
.totals .pay{font-weight:800;font-size:19px;border:2px solid #111;padding:8px 10px;margin-top:8px}
.totals .pay .v{font-size:19px;font-weight:700}
.foot{font-size:14px;line-height:1.9}
.barcode{height:54px;margin:14px auto 4px;width:300px;
  background:repeating-linear-gradient(90deg,#111 0 2px,#fff 2px 4px,#111 4px 7px,#fff 7px 8px,#111 8px 9px,#fff 9px 12px,#111 12px 13px,#fff 13px 16px)}
`;
  const body = `
<div class="paper">
  <div class="center logo">فروشگاه زنجیره‌ای نمونه</div>
  <div class="center sub">شعبه ${fa(12)} — تهران، خیابان نمونه، پلاک ${fa(45)}<br>تلفن: <span class="ltr">${fa('021-12345678')}</span></div>
  <div class="dash"></div>
  <div class="meta"><span>تاریخ: ${fa(d.date)}</span><span>ساعت: ${fa(d.time)}</span></div>
  <div class="meta"><span>شماره فاکتور: ${fa(845217)}</span><span>صندوق: ${fa(3)}</span></div>
  <div class="dash"></div>
  <table>
    <colgroup><col style="width:38px"><col><col style="width:52px"><col style="width:104px"><col style="width:112px"></colgroup>
    <thead><tr><th class="c">ردیف</th><th>شرح کالا</th><th class="c">تعداد</th><th class="l">فی (ریال)</th><th class="l">مبلغ (ریال)</th></tr></thead>
    <tbody>
${rows}
    </tbody>
  </table>
  <div class="dash"></div>
  <div class="totals">
    <div class="t"><span>جمع کل (ریال):</span><span class="v">${money(d.subtotal)}</span></div>
    <div class="t"><span>تخفیف (ریال):</span><span class="v">${money(d.discount)}</span></div>
    <div class="t"><span>مالیات بر ارزش افزوده ${fa(10)}٪ (ریال):</span><span class="v">${money(d.tax)}</span></div>
    <div class="t pay"><span>مبلغ قابل پرداخت (ریال):</span><span class="v">${money(d.payable)}</span></div>
  </div>
  <div class="dash"></div>
  <div class="foot">تعداد اقلام: ${fa(d.items.length)}<br>نوع پرداخت: کارت بانکی</div>
  <div class="barcode"></div>
  <div class="center sub">از خرید شما سپاسگزاریم</div>
</div>`;
  return page(css, body);
}

// ---------------------------------------------------------------------------
// 2. Café receipt — no currency unit anywhere, Persian digits
// ---------------------------------------------------------------------------

const cafe = (() => {
  const rows = [
    { name: 'کاپوچینو', quantity: 2, unit_price: 1_850_000 },
    { name: 'کروسان کره‌ای', quantity: 1, unit_price: 1_200_000 },
    { name: 'چای ماسالا', quantity: 1, unit_price: 1_400_000 },
    { name: 'کیک شکلاتی', quantity: 1, unit_price: 1_650_000 },
    { name: 'آب معدنی کوچک', quantity: 2, unit_price: 250_000 },
  ];
  const items = rows.map((r) => ({ ...r, line_total: r.quantity * r.unit_price }));
  const subtotal = sum(items.map((i) => i.line_total));
  assert(items.length === 5, 'cafe must have 5 items');
  return {
    file: 'receipt_cafe_no_unit.png',
    store: 'کافه نمونه',
    date: '1405/07/09',
    time: '10:15',
    items,
    subtotal,
    payable: subtotal,
  };
})();

function cafeHtml(d) {
  const rows = d.items
    .map(
      (it) => `<tr><td>${it.name}</td><td class="c">${fa(it.quantity)}</td><td class="num">${faMoney(it.line_total)}</td></tr>`
    )
    .join('\n');
  const css = `
body{background:#cfc6b8;padding:34px 0 40px}
.paper{width:440px;margin:0 auto;background:#fffdf8;color:#151515;padding:26px 26px 30px;
  box-shadow:0 3px 14px rgba(0,0,0,.25)}
.center{text-align:center}
.logo{font-size:27px;font-weight:800}
.sub{font-size:14px;color:#333;line-height:1.8}
.dash{border-top:2px dashed #333;margin:12px 0}
.meta{display:flex;justify-content:space-between;font-size:15px;line-height:1.9}
table{width:100%;border-collapse:collapse;font-size:17px}
th{font-size:15px;font-weight:700;padding:6px 2px;border-bottom:1.5px dashed #333;text-align:right}
td{padding:7px 2px}
.c{text-align:center}
th.l,.num{text-align:left}
.num{direction:ltr;font-weight:500}
.total{display:flex;justify-content:space-between;font-size:21px;font-weight:800;padding:6px 0}
.total .num{font-weight:800}
`;
  const body = `
<div class="paper">
  <div class="center logo">کافه نمونه</div>
  <div class="center sub">کافه و شیرینی‌پزی</div>
  <div class="dash"></div>
  <div class="meta"><span>تاریخ: ${fa(d.date)}</span><span>ساعت: ${fa(d.time)}</span></div>
  <div class="meta"><span>میز: ${fa(4)}</span><span>سفارش: ${fa(57)}</span></div>
  <div class="dash"></div>
  <table>
    <thead><tr><th>شرح</th><th class="c">تعداد</th><th class="l">مبلغ</th></tr></thead>
    <tbody>
${rows}
    </tbody>
  </table>
  <div class="dash"></div>
  <div class="total"><span>جمع:</span><span class="num">${faMoney(d.subtotal)}</span></div>
  <div class="dash"></div>
  <div class="center sub">نوش جان! منتظر دیدار دوباره‌ی شما هستیم</div>
</div>`;
  return page(css, body);
}

// ---------------------------------------------------------------------------
// 3. Bank SMS conversation — 4 withdrawals, 1 deposit, Latin digits
// ---------------------------------------------------------------------------

const bankSms = (() => {
  const account = '1234**5678';
  const openingBalance = 41_510_000;
  // chronological order == top-to-bottom order on screen
  const msgs = [
    { type: 'withdrawal', amount: 640_000, date: '1405/07/08', time: '09:15', description: 'خرید' },
    { type: 'withdrawal', amount: 3_800_000, date: '1405/07/08', time: '20:47', description: 'انتقال' },
    { type: 'deposit', amount: 25_000_000, date: '1405/07/09', time: '08:30', description: null },
    { type: 'withdrawal', amount: 12_500_000, date: '1405/07/09', time: '11:05', description: null },
    { type: 'withdrawal', amount: 1_250_000, date: '1405/07/09', time: '13:42', description: 'خرید' },
  ];
  let bal = openingBalance;
  for (const m of msgs) {
    bal += m.type === 'deposit' ? m.amount : -m.amount;
    m.balance_after = bal;
  }
  assert(msgs.filter((m) => m.type === 'withdrawal').length === 4, '4 withdrawals');
  assert(msgs.filter((m) => m.type === 'deposit').length === 1, '1 deposit');
  assert(msgs.filter((m) => m.date === '1405/07/08').length === 2, 'two SMS dated 1405/07/08');
  return { file: 'bank_sms_list.png', bank: 'بانک نمونه', account, msgs };
})();

function bankSmsHtml(d) {
  const dayLabel = { '1405/07/08': 'چهارشنبه ۸ مهر ۱۴۰۵', '1405/07/09': 'پنجشنبه ۹ مهر ۱۴۰۵' };
  let lastDate = null;
  const parts = [];
  for (const m of d.msgs) {
    if (m.date !== lastDate) {
      parts.push(`<div class="day"><span>${dayLabel[m.date]}</span></div>`);
      lastDate = m.date;
    }
    const lines = [`<div>${d.bank}</div>`];
    if (m.description) lines.push(`<div>${m.description}</div>`);
    if (m.type === 'withdrawal') lines.push(`<div>برداشت: <span class="ltr">${money(m.amount)}-</span></div>`);
    else lines.push(`<div>واریز: <span class="ltr">${money(m.amount)}+</span></div>`);
    lines.push(`<div>حساب: <span class="ltr">${d.account}</span></div>`);
    lines.push(`<div>مانده: <span class="ltr">${money(m.balance_after)}</span></div>`);
    lines.push(`<div><span class="ltr">${m.date}</span></div>`);
    lines.push(`<div><span class="ltr">${m.time}</span></div>`);
    parts.push(`<div class="msg"><div class="bubble">${lines.join('')}</div><div class="when">${fa(m.time)}</div></div>`);
  }
  const css = `
body{background:#fff;width:720px;min-height:100vh;display:flex;flex-direction:column;color:#1b1b1f}
.status{height:46px;display:flex;align-items:center;justify-content:space-between;padding:0 22px;font-size:17px;font-weight:500;background:#f7f7f9}
.status .icons{display:flex;align-items:center;gap:10px}
.sig{display:flex;align-items:flex-end;gap:2px;height:16px}
.sig i{display:block;width:4px;background:#222;border-radius:1px}
.batt{width:30px;height:15px;border:2px solid #222;border-radius:4px;position:relative;padding:1px}
.batt::after{content:"";position:absolute;left:-5px;top:3px;width:3px;height:6px;background:#222;border-radius:1px}
.batt b{display:block;height:100%;width:78%;background:#222;border-radius:1px;margin-right:auto;margin-left:0}
.bar{height:76px;display:flex;align-items:center;gap:16px;padding:0 18px;border-bottom:1px solid #e3e3e8;background:#f7f7f9}
.back{font-size:30px;color:#333;line-height:1}
.avatar{width:46px;height:46px;border-radius:50%;background:#1f6fd1;color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:22px}
.title{font-size:21px;font-weight:700}
.title small{display:block;font-size:13.5px;font-weight:400;color:#777}
.thread{padding:10px 22px 24px;display:flex;flex-direction:column}
.day{text-align:center;margin:18px 0 10px}
.day span{font-size:14.5px;color:#666;background:#f0f0f3;border-radius:14px;padding:4px 14px}
.msg{display:flex;flex-direction:column;align-items:flex-start;margin:8px 0}
.bubble{background:#e9e9ee;border-radius:24px;border-top-right-radius:8px;padding:14px 20px;font-size:20px;line-height:1.6;max-width:440px;min-width:300px}
.when{font-size:13.5px;color:#888;margin:5px 10px 0}
.input{margin-top:auto;display:flex;align-items:center;gap:12px;padding:14px 20px 26px;border-top:1px solid #e3e3e8;background:#fafafa}
.input .box{flex:1;background:#efeff3;border-radius:24px;padding:12px 20px;color:#999;font-size:16px}
`;
  const body = `
<div class="status"><span>${fa('13:45')}</span><span class="icons"><span class="sig"><i style="height:5px"></i><i style="height:8px"></i><i style="height:12px"></i><i style="height:16px"></i></span><span style="font-size:14px">${fa('84')}٪</span><span class="batt"><b></b></span></span></div>
<div class="bar"><span class="back">→</span><span class="avatar">ب</span><span class="title">${d.bank}<small>پیامک</small></span></div>
<div class="thread">${parts.join('\n')}</div>
<div class="input"><div class="box">امکان پاسخ به این فرستنده وجود ندارد</div></div>`;
  return page(css, body);
}

// ---------------------------------------------------------------------------
// 4. Mobile-banking "گردش حساب" — 4 withdrawals (red, -) and 2 deposits (green, +)
// ---------------------------------------------------------------------------

const bankApp = (() => {
  const openingBalance = 42_350_000;
  // on-screen order: newest first
  const rows = [
    { type: 'withdrawal', amount: 6_200_000, description: 'خرید کارتخوان - رستوران نمونه', date: '1405/07/08', time: '21:10' },
    { type: 'deposit', amount: 20_000_000, description: 'انتقال از حساب دیگر', date: '1405/07/07', time: '10:32' },
    { type: 'withdrawal', amount: 7_960_000, description: 'خرید اینترنتی - دیجی‌کالا', date: '1405/07/06', time: '23:05' },
    { type: 'withdrawal', amount: 1_870_000, description: 'پرداخت قبض برق', date: '1405/07/04', time: '09:48' },
    { type: 'withdrawal', amount: 15_000_000, description: 'کارت به کارت', date: '1405/07/03', time: '17:26' },
    { type: 'deposit', amount: 185_000_000, description: 'واریز حقوق', date: '1405/07/01', time: '08:00' },
  ];
  const balance =
    openingBalance +
    sum(rows.filter((r) => r.type === 'deposit').map((r) => r.amount)) -
    sum(rows.filter((r) => r.type === 'withdrawal').map((r) => r.amount));
  assert(rows.filter((r) => r.type === 'withdrawal').length === 4, '4 withdrawals');
  assert(rows.filter((r) => r.type === 'deposit').length === 2, '2 deposits');
  return { file: 'mobile_bank_history.png', bank: 'همراه بانک نمونه', account: '0212-3456-7801', balance, rows };
})();

function bankAppHtml(d) {
  const rows = d.rows
    .map((r) => {
      const w = r.type === 'withdrawal';
      return `<div class="row">
  <div class="ic ${w ? 'out' : 'in'}">${w ? '↑' : '↓'}</div>
  <div class="info"><div class="desc">${r.description}</div><div class="dt">${fa(r.date)} &nbsp;|&nbsp; ${fa(r.time)}</div></div>
  <div class="amt ${w ? 'neg' : 'pos'}"><span class="ltr">${w ? '-' : '+'}${faMoney(r.amount)}</span></div>
</div>`;
    })
    .join('\n');
  const css = `
body{background:#f2f4f8;width:720px;min-height:100vh;display:flex;flex-direction:column;color:#1d2433}
.status{height:46px;display:flex;align-items:center;justify-content:space-between;padding:0 22px;font-size:17px;font-weight:500;color:#fff;background:#0d47a1}
.status .icons{display:flex;align-items:center;gap:10px}
.batt{width:30px;height:15px;border:2px solid #fff;border-radius:4px;padding:1px;position:relative}
.batt::after{content:"";position:absolute;left:-5px;top:3px;width:3px;height:6px;background:#fff;border-radius:1px}
.batt b{display:block;height:100%;width:62%;background:#fff;border-radius:1px}
.head{background:linear-gradient(160deg,#0d47a1,#1976d2);color:#fff;padding:14px 24px 86px}
.nav{display:flex;align-items:center;justify-content:space-between;height:56px}
.nav .t{font-size:23px;font-weight:700}
.nav .b{font-size:30px;line-height:1}
.nav .m{font-size:26px;line-height:1;letter-spacing:-2px}
.card{background:#fff;border-radius:20px;margin:-74px 20px 0;padding:20px 22px;box-shadow:0 6px 20px rgba(13,71,161,.15)}
.card .acc{display:flex;justify-content:space-between;font-size:15.5px;color:#5b6475}
.card .bal{margin-top:10px;font-size:15px;color:#5b6475}
.card .bal b{display:block;font-size:27px;color:#1d2433;margin-top:4px}
.filters{display:flex;gap:10px;padding:18px 20px 6px;flex-wrap:wrap}
.chip{background:#fff;border:1px solid #d6dbe6;border-radius:18px;padding:6px 16px;font-size:14.5px;color:#3c4659}
.chip.on{background:#e3edfc;border-color:#9dbcf0;color:#0d47a1;font-weight:700}
.lh{display:flex;justify-content:space-between;padding:14px 30px 8px;font-size:15px;color:#6b7487;font-weight:700}
.list{margin:0 20px 22px;background:#fff;border-radius:20px;overflow:hidden;box-shadow:0 2px 10px rgba(0,0,0,.05)}
.row{display:flex;align-items:center;gap:16px;padding:18px 20px;border-bottom:1px solid #eef0f4}
.row:last-child{border-bottom:none}
.ic{width:48px;height:48px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:700;flex:none}
.ic.out{background:#fdecec;color:#d32f2f}
.ic.in{background:#e7f6ea;color:#2e7d32}
.info{flex:1;min-width:0}
.desc{font-size:18.5px;font-weight:700}
.dt{font-size:14.5px;color:#7a8295;margin-top:4px}
.amt{font-size:20px;font-weight:800;white-space:nowrap}
.neg{color:#d32f2f}
.pos{color:#2e7d32}
.tabs{display:flex;justify-content:space-around;background:#fff;margin-top:auto;padding:12px 0 22px;border-top:1px solid #e3e6ee}
.tab{text-align:center;font-size:13.5px;color:#7a8295}
.tab i{display:block;width:26px;height:26px;margin:0 auto 4px;border-radius:8px;background:#cfd6e4}
.tab.on{color:#0d47a1;font-weight:700}
.tab.on i{background:#0d47a1}
`;
  const body = `
<div class="status"><span>${fa('09:41')}</span><span class="icons"><span style="font-size:14px">${fa('62')}٪</span><span class="batt"><b></b></span></span></div>
<div class="head">
  <div class="nav"><span class="b">→</span><span class="t">گردش حساب</span><span class="m">⋮</span></div>
</div>
<div class="card">
  <div class="acc"><span>${d.bank} — حساب جاری</span><span class="ltr">${fa(d.account)}</span></div>
  <div class="bal">موجودی قابل برداشت (ریال)<b>${faMoney(d.balance)}</b></div>
</div>
<div class="filters"><span class="chip on">همه تراکنش‌ها</span><span class="chip">واریز</span><span class="chip">برداشت</span><span class="chip">مهر ${fa(1405)}</span></div>
<div class="lh"><span>شرح تراکنش</span><span>مبلغ (ریال)</span></div>
<div class="list">
${rows}
</div>
<div class="tabs"><div class="tab"><i></i>خانه</div><div class="tab"><i></i>کارت‌ها</div><div class="tab on"><i></i>حساب‌ها</div><div class="tab"><i></i>خدمات</div></div>`;
  return page(css, body);
}

// ---------------------------------------------------------------------------
// 5. Non-financial picture
// ---------------------------------------------------------------------------

const notFinancial = { file: 'not_financial.png', caption: 'سفر شمال، تابستان' };

function notFinancialHtml(d) {
  const trees = Array.from({ length: 16 }, (_, i) => {
    const x = 10 + i * 44 + (i % 3) * 7;
    const y = 470 + (i % 4) * 9;
    const h = 70 + (i % 5) * 12;
    return `<polygon points="${x},${y} ${x + 22},${y - h} ${x + 44},${y}" fill="${i % 2 ? '#2f6b3d' : '#25593a'}"/>`;
  }).join('');
  const css = `
body{background:#efe6d6;width:720px;padding:34px 0 40px}
.photo{width:640px;margin:0 auto;background:#fff;padding:18px 18px 26px;box-shadow:0 6px 22px rgba(0,0,0,.2);transform:rotate(-1.2deg)}
svg{display:block}
.cap{text-align:center;font-size:36px;font-weight:700;color:#2b3a4a;margin-top:18px}
.sub{text-align:center;font-size:19px;color:#6a7480;margin-top:4px}
`;
  const body = `
<div class="photo">
<svg width="604" height="600" viewBox="0 0 720 715" preserveAspectRatio="xMidYMid slice">
  <defs>
    <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#5fb4ea"/><stop offset="1" stop-color="#cfeefc"/></linearGradient>
    <linearGradient id="sea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2f8fc6"/><stop offset="1" stop-color="#1b5f8f"/></linearGradient>
  </defs>
  <rect width="720" height="715" fill="url(#sky)"/>
  <circle cx="560" cy="130" r="84" fill="#fff6c2" opacity=".55"/>
  <circle cx="560" cy="130" r="62" fill="#ffd54a"/>
  <g fill="#fff" opacity=".92"><ellipse cx="160" cy="120" rx="70" ry="24"/><ellipse cx="205" cy="104" rx="46" ry="26"/><ellipse cx="395" cy="190" rx="60" ry="18"/><ellipse cx="430" cy="178" rx="36" ry="20"/></g>
  <path d="M0 380 L120 250 L210 330 L330 210 L450 320 L560 240 L720 360 L720 470 L0 470 Z" fill="#7aa58c"/>
  <path d="M330 210 L370 247 L352 240 L338 252 L318 238 L290 250 Z" fill="#f4f7f8"/>
  <path d="M0 430 Q120 360 250 420 T520 410 T720 400 L720 520 L0 520 Z" fill="#3f8a52"/>
  ${trees}
  <path d="M0 500 Q180 480 360 505 T720 500 L720 560 L0 560 Z" fill="#ecd9a0"/>
  <rect y="550" width="720" height="165" fill="url(#sea)"/>
  <g stroke="#bfe4f7" stroke-width="3" fill="none" opacity=".8" stroke-linecap="round">
    <path d="M40 600 q20 -10 40 0 t40 0"/><path d="M260 630 q20 -10 40 0 t40 0"/><path d="M480 610 q20 -10 40 0 t40 0"/><path d="M150 670 q20 -10 40 0 t40 0"/><path d="M560 680 q20 -10 40 0 t40 0"/>
  </g>
  <g transform="translate(400 585)"><path d="M0 0 L90 0 L75 18 L12 18 Z" fill="#c0392b"/><path d="M45 0 L45 -60 L78 -6 Z" fill="#fff"/><rect x="43" y="-62" width="3" height="62" fill="#5d4037"/></g>
  <g stroke="#333" stroke-width="2.5" fill="none"><path d="M180 90 q8 -8 16 0 q8 -8 16 0"/><path d="M230 70 q6 -6 12 0 q6 -6 12 0"/></g>
</svg>
<div class="cap">${d.caption}</div>
<div class="sub">جاده‌ی چالوس و دریای خزر</div>
</div>`;
  return page(css, body);
}

// ---------------------------------------------------------------------------
// ground truth
// ---------------------------------------------------------------------------

const pickItems = (items) =>
  items.map(({ name, quantity, unit_price, line_total }) => ({ name, quantity, unit_price, line_total }));

const expected = {
  [supermarket.file]: {
    kind: 'receipt',
    unit: 'rial',
    store: supermarket.store,
    date: supermarket.date,
    time: supermarket.time,
    items: pickItems(supermarket.items),
    subtotal: supermarket.subtotal,
    discount: supermarket.discount,
    tax: supermarket.tax,
    payable: supermarket.payable,
  },
  [cafe.file]: {
    kind: 'receipt',
    unit: 'rial',
    unit_printed: false,
    store: cafe.store,
    date: cafe.date,
    time: cafe.time,
    items: pickItems(cafe.items),
    subtotal: cafe.subtotal,
    payable: cafe.payable,
  },
  [bankSms.file]: {
    kind: 'bank_sms',
    unit: 'rial',
    bank: bankSms.bank,
    account: bankSms.account,
    withdrawals: bankSms.msgs
      .filter((m) => m.type === 'withdrawal')
      .map(({ amount, date, time, description, balance_after }) => ({ amount, date, time, description, balance_after })),
    deposits: bankSms.msgs
      .filter((m) => m.type === 'deposit')
      .map(({ amount, date, time, description, balance_after }) => ({ amount, date, time, description, balance_after })),
  },
  [bankApp.file]: {
    kind: 'bank_app',
    unit: 'rial',
    balance: bankApp.balance,
    withdrawals: bankApp.rows
      .filter((r) => r.type === 'withdrawal')
      .map(({ amount, description, date, time }) => ({ amount, description, date, time })),
    deposits: bankApp.rows
      .filter((r) => r.type === 'deposit')
      .map(({ amount, description, date, time }) => ({ amount, description, date, time })),
  },
  [notFinancial.file]: { kind: 'not_financial' },
};

// ---------------------------------------------------------------------------
// rendering
// ---------------------------------------------------------------------------

async function loadPlaywright() {
  let mod;
  try {
    mod = await import('playwright');
  } catch {
    const root = execSync('npm root -g').toString().trim();
    mod = createRequire(path.join(root, 'noop.js'))('playwright');
  }
  return mod.chromium ? mod : mod.default;
}

const jobs = [
  { file: supermarket.file, html: supermarketHtml(supermarket), width: 680, height: 200, dpr: 1.5,
    mustContain: [...supermarket.items.map((i) => money(i.line_total)), money(supermarket.payable)] },
  { file: cafe.file, html: cafeHtml(cafe), width: 600, height: 200, dpr: 1.5,
    mustContain: [...cafe.items.map((i) => faMoney(i.line_total)), faMoney(cafe.payable)],
    mustNotContain: ['ریال', 'تومان', 'ريال'] },
  { file: bankSms.file, html: bankSmsHtml(bankSms), width: 720, height: 1560, dpr: 1.5,
    mustContain: bankSms.msgs.map((m) => money(m.amount)) },
  { file: bankApp.file, html: bankAppHtml(bankApp), width: 720, height: 1200, dpr: 1.5,
    mustContain: [...bankApp.rows.map((r) => faMoney(r.amount)), 'مبلغ (ریال)'] },
  { file: notFinancial.file, html: notFinancialHtml(notFinancial), width: 720, height: 200, dpr: 1.25,
    mustContain: [notFinancial.caption], mustNotMatch: /[0-9۰-۹]/ },
];

const { chromium } = await loadPlaywright();
const proxyUrl = process.env.HTTPS_PROXY || process.env.https_proxy;
const browser = await chromium.launch(proxyUrl ? { proxy: { server: proxyUrl } } : {});
const fontCache = new Map();

try {
  for (const job of jobs) {
    const ctx = await browser.newContext({ viewport: { width: job.width, height: job.height }, deviceScaleFactor: job.dpr });
    // Fetch Google Fonts from Node (trusts NODE_EXTRA_CA_CERTS) and hand them to Chromium.
    await ctx.route(/^https:\/\/fonts\.(googleapis|gstatic)\.com\//, async (route) => {
      const url = route.request().url();
      if (!fontCache.has(url)) {
        const resp = await route.fetch();
        if (!resp.ok()) throw new Error(`font fetch failed ${resp.status()} ${url}`);
        fontCache.set(url, { status: resp.status(), headers: resp.headers(), body: await resp.body() });
      }
      await route.fulfill(fontCache.get(url));
    });
    const pg = await ctx.newPage();
    await pg.setContent(job.html, { waitUntil: 'networkidle' });
    const fontsOk = await pg.evaluate(async () => {
      await document.fonts.ready;
      const r = await Promise.all([
        document.fonts.load('400 16px Vazirmatn', 'سلام'),
        document.fonts.load('700 16px Vazirmatn', 'سلام'),
      ]);
      return r.every((faces) => faces.length > 0 && faces.every((f) => f.status === 'loaded'));
    });
    if (!fontsOk) throw new Error(`Vazirmatn did not load for ${job.file}`);

    const text = await pg.evaluate(() => document.body.innerText);
    for (const s of job.mustContain || []) if (!text.includes(s)) throw new Error(`${job.file}: rendered text lacks "${s}"`);
    for (const s of job.mustNotContain || []) if (text.includes(s)) throw new Error(`${job.file}: rendered text must not contain "${s}"`);
    if (job.mustNotMatch && job.mustNotMatch.test(text)) throw new Error(`${job.file}: rendered text must not contain digits`);

    const out = path.join(OUT_DIR, job.file);
    await pg.screenshot({ path: out, fullPage: true });
    const { width, height } = await pg.evaluate(() => ({
      width: document.documentElement.scrollWidth,
      height: document.documentElement.scrollHeight,
    }));
    console.log(
      `${job.file}: ${Math.round(width * job.dpr)}x${Math.round(height * job.dpr)} px, ${(statSync(out).size / 1024).toFixed(0)} KB`
    );
    await ctx.close();
  }
} finally {
  await browser.close();
}

writeFileSync(path.join(OUT_DIR, 'expected.json'), JSON.stringify(expected, null, 2) + '\n');
console.log('expected.json written');
