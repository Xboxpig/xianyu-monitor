import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { parseSearch, parseDetail, parseSeller, collectSnapshot, readPage, interactPage } from "../collector.js";

test("search response preserves price, tags, seller and publication time", () => {
  const fixture = JSON.parse(readFileSync(new URL("../../tests/fixtures/search_results.json", import.meta.url)));
  const [item] = parseSearch(fixture);
  assert.equal(item.id, "123456");
  assert.equal(item.price, "¥13999");
  assert.equal(item.title, "Sony A7M4 Body");
  assert.equal(item.area, "Shanghai");
  assert.deepEqual(item.tags, ["验货宝", "包邮"]);
  assert.match(item.publish_time, /^\d{4}-/);
});

test("detail parsing verifies requested ID and keeps description and seller", () => {
  const fixture = {
    data: { itemDO: { itemId: 123, title: "Camera", desc: "Excellent condition", price: "100",
      imageInfos: [{ url: "//img.alicdn.com/item.jpg" }, { url: "http://127.0.0.1/private" }] },
      sellerDO: { sellerId: 456, nick: "Seller", isBiz: false, userRegDay: 365 } },
  };
  assert.equal(parseDetail(fixture, "999"), null);
  const item = parseDetail(fixture, "123");
  assert.equal(item.description, "Excellent condition");
  assert.equal(item.seller_id, "456");
  assert.equal(item.personal, true);
  assert.deepEqual(item.images, ["https://img.alicdn.com/item.jpg"]);
});

test("seller lists and ratings are deduplicated", () => {
  const head = { url: "mtop.idle.web.user.page.head", body: { data: { module: {
    base: { displayName: "Seller", introduction: "hi", ylzTags: [{ attributes: { role: "seller" }, text: "优秀" }] },
    tabs: { item: { number: 2 }, rate: { number: 1 } },
  } } } };
  const items = { url: "mtop.idle.web.xyh.item.list", body: { data: { cardList: [{ cardData: { id: "1", title: "Camera", itemStatus: 0 } }] } } };
  const ratings = { url: "mtop.idle.web.trade.rate.list", body: { data: { cardList: [{ cardData: { rateId: "r1", rate: 1, rateTagList: [{ text: "卖家" }] } }] } } };
  const seller = parseSeller([head, items, items, ratings, ratings]);
  assert.equal(seller["卖家信用等级"], "优秀");
  assert.equal(seller["卖家发布的商品列表"].length, 1);
  assert.equal(seller["卖家收到的评价列表"].length, 1);
  assert.equal(seller["作为卖家的好评率"], "100.00%");
});

test("empty pages, blocked pages and unavailable data are distinct", () => {
  const command = { action: "search", task: {} };
  assert.equal(collectSnapshot({ entries: [], domItems: [], empty: false }, command), null);
  assert.deepEqual(collectSnapshot({ entries: [], domItems: [], empty: true, hasMore: false }, command).items, []);
  assert.equal(collectSnapshot({ attention: "login required" }, command).outcome, "attention");
  assert.equal(collectSnapshot({ entries: [{ url: "idlemtopsearch.pc.search", body: { ret: ["FAIL_SYS_BUSY"] } }] }, command).outcome, "attention");
});

test('cleared verification and newer successful responses stop blocking a page', () => {
  const saved = Object.fromEntries(['window', 'document', 'location', 'getComputedStyle'].map(key => [key, globalThis[key]]));
  let visible = false;
  globalThis.location = { href: 'https://www.goofish.com/personal?userId=1' };
  globalThis.window = { __xianyuNativeCapture: { sequence: 2, entries: [
    { url: 'https://h5api.m.goofish.com/h5/mtop.idle.web.user.page.head/1.0/?callback=1', body: { ret: ['FAIL_SYS_USER_VALIDATE'] }, sequence: 1 },
    { url: 'https://h5api.m.goofish.com/h5/mtop.idle.web.user.page.head/1.0/?callback=2', body: { ret: ['SUCCESS'] }, sequence: 2 },
  ] } };
  globalThis.document = { readyState: 'complete', body: { innerText: '' },
    querySelectorAll: selector => selector === '[role="dialog"]' ? [] : [{ getClientRects: () => visible ? [1] : [] }] };
  globalThis.getComputedStyle = () => ({ visibility: 'visible' });
  try {
    assert.equal(readPage('seller').ready, true);
    visible = true;
    assert.ok(readPage('seller').attention);
    visible = false;
    window.__xianyuNativeCapture.entries.pop();
    assert.ok(readPage('seller').attention);
  } finally { for (const [key, value] of Object.entries(saved)) globalThis[key] = value; }
});

async function withFilterPage(run) {
  const keys = ['window', 'document', 'getComputedStyle', 'setTimeout', 'MouseEvent'];
  const saved = Object.fromEntries(keys.map(key => [key, globalThis[key]]));
  const nodes = [];
  const page = { elapsed: 0, advance: () => {}, nodes, capture: { sequence: 3, entries: [] } };
  page.add = (text, settings = {}) => {
    const element = {
      textContent: text, className: '', children: [], parentElement: null,
      getClientRects: () => element.hidden ? [] : [1],
      getAttribute: name => element.attributes?.[name] || null,
      querySelector: () => null, closest: () => null,
      click: () => {}, dispatchEvent: () => {}, ...settings,
    };
    nodes.push(element);
    return element;
  };
  globalThis.window = { __xianyuNativeCapture: page.capture };
  globalThis.document = { querySelectorAll: selector => selector === '[class*="search-select-container"]' ?
    nodes.filter(node => node.dropdown) : nodes.filter(node => selector.includes('div') || node.tagName !== 'DIV') };
  globalThis.getComputedStyle = () => ({ visibility: 'visible' });
  globalThis.MouseEvent = class { constructor(type) { this.type = type; } };
  globalThis.setTimeout = (callback, delay) => { page.elapsed += delay; page.advance(); callback(); };
  try { await run(page); }
  finally { for (const key of keys) globalThis[key] = saved[key]; }
}

test('publication filter selects div menu items after delayed rendering', async () => {
  await withFilterPage(async page => {
    let openedAt;
    let clicks = 0;
    page.add('新发布', { tagName: 'DIV', click: () => { openedAt = page.elapsed; } });
    const option = page.add('最新', { tagName: 'DIV', hidden: true, click() { clicks++; this.className = 'selected--abc'; } });
    // Both ancestors and the leaf match text, as in custom React dropdowns.
    page.add('最新', { tagName: 'DIV', children: [option], click: () => assert.fail('clicked menu wrapper') });
    page.advance = () => { if (openedAt != null && page.elapsed - openedAt >= 1000) option.hidden = false; };
    const opening = await interactPage('filters', { new_publish_option: '最新' });
    assert.equal(opening.pending, true);
    page.elapsed = 1000;
    page.advance();
    const result = await interactPage('filters', { new_publish_option: '最新' });
    assert.equal(result.retry, undefined);
    assert.equal(result.changed, true);
    assert.equal(result.sequenceFloor, 3);
    assert.equal(clicks, 1);
    assert.ok(page.elapsed >= 1000);
  });
});

test('hover menus and all publication ranges are selected automatically', async () => {
  for (const label of ['最新', '1天内', '3天内', '7天内', '14天内']) {
    await withFilterPage(async page => {
      const option = page.add(label, { tagName: 'DIV', hidden: true, click() { this.attributes = { 'aria-selected': 'true' }; } });
      page.add('新发布', { tagName: 'DIV', dispatchEvent(event) { if (event.type === 'mouseover') option.hidden = false; } });
      await interactPage('filters', { new_publish_option: label });
      const result = await interactPage('filters', { new_publish_option: label });
      assert.equal(result.retry, undefined, label);
      assert.equal(result.changed, true, label);
    });
  }
});

test('selected publication filter is left in place without toggling or waiting for another response', async () => {
  await withFilterPage(async page => {
    page.add('最新', { tagName: 'DIV', attributes: { 'aria-selected': 'true' }, click: () => assert.fail('already selected') });
    const result = await interactPage('filters', { new_publish_option: '最新' });
    assert.equal(result.changed, false);
    assert.equal(page.elapsed, 0);
  });
});

test('missing or ineffective publication controls request automatic retry', async () => {
  for (const ineffective of [false, true]) {
    await withFilterPage(async page => {
      if (ineffective) {
        page.add('新发布', { tagName: 'DIV' });
        page.add('最新', { tagName: 'DIV' });
      }
      let result = await interactPage('filters', { new_publish_option: '最新' });
      if (ineffective) {
        assert.equal(result.pending, true);
        window.__xianyuNativePublishFilter.clickedAt -= 15001;
        result = await interactPage('filters', { new_publish_option: '最新' });
      }
      assert.equal(result.retry, true);
      assert.equal(result.attention, undefined);
      assert.match(result.error, /未找到菜单入口|未确认筛选生效/);
    });
  }
});

test('CSS hover-only publication menu is selected while hidden and never toggled off', async () => {
  for (const label of ['最新', '1天内', '3天内', '7天内', '14天内']) {
    await withFilterPage(async page => {
      let clicks = 0;
      let nextTitle;
      const title = page.add('新发布');
      const items = ['最新', '1天内', '3天内', '7天内', '14天内'].map(text => page.add(text, {
        hidden: true, click() { clicks++; nextTitle = title.textContent === text ? '新发布' : text; },
      }));
      const menu = { children: items };
      page.add(items.map(item => item.textContent).join(''), { dropdown: true,
        querySelector: selector => selector.includes('items-container') ? menu : title });
      const started = await interactPage('filters', { new_publish_option: label });
      assert.equal(started.pending, true);
      assert.equal(clicks, 1);
      // Before React commits, another observation must not cancel the choice.
      assert.equal((await interactPage('filters', { new_publish_option: label })).pending, true);
      assert.equal(clicks, 1);
      title.textContent = nextTitle;
      const applied = await interactPage('filters', { new_publish_option: label });
      assert.equal(applied.pending, undefined);
      assert.equal(applied.retry, undefined);
      assert.equal(clicks, 1);
      assert.equal(page.elapsed, 0, 'publication logic must not wait on page timers');
    });
  }
});

test('Goofish nested checked indicators prevent toggling personal filters on a subsequent tick', async () => {
  await withFilterPage(async page => {
    let checked = false;
    let clicks = 0;
    const container = page.add('个人闲置', { children: [{ textContent: '个人闲置' }],
      querySelector: selector => selector.includes('search-checkbox-checked--') && checked ? {} : null });
    page.add('个人闲置', { parentElement: container, click() { clicks++; checked = !checked; } });
    await interactPage('filters', { personal_only: true });
    const result = await interactPage('filters', { personal_only: true });
    assert.equal(result.personalApplied, true);
    assert.equal(clicks, 1);
  });
});

test('the final filter response must be newer than earlier checkbox responses', async () => {
  await withFilterPage(async page => {
    page.add('个人闲置', { click() { this.className = 'checked'; page.capture.sequence = 4; } });
    page.add('新发布');
    page.add('最新', { tagName: 'DIV', click() {
      page.capture.sequence = 5;
      page.capture.entries.push({ url: 'idlemtopsearch.pc.search', sequence: 5, body: { ret: ['SUCCESS::OK'] } });
    } });
    const result = await interactPage('filters', { personal_only: true, new_publish_option: '最新' });
    assert.equal(result.retry, undefined);
    assert.equal(result.personalApplied, true);
    assert.equal(result.sequenceFloor, 4);
  });
});
