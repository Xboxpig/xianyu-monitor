import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { parseSearch, parseDetail, parseSeller, collectSnapshot, readPage } from "../collector.js";

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
