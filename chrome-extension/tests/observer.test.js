import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

test("JSONP responses are captured while the page callback keeps its arguments and return value", () => {
  let mutation;
  const calls = [];
  const page = {
    fetch: () => Promise.reject(new Error("unused")),
    mtopjsonp1(...args) { calls.push([this, args]); return "page-result"; },
  };
  const context = {
    window: page, URL,
    document: { querySelectorAll: () => [] },
    MutationObserver: class { constructor(callback) { mutation = callback; } observe() {} },
    XMLHttpRequest: class { open() {} },
  };
  vm.runInNewContext(fs.readFileSync(new URL("../page-observer.js", import.meta.url), "utf8"), context);
  const script = { tagName: "SCRIPT", src: "https://h5api.m.goofish.com/h5/mtop.taobao.idle.pc.detail/1.0/?callback=mtopjsonp1" };
  mutation([{ type: "childList", addedNodes: [script] }]);
  mutation([{ type: "attributes", target: script }]);
  const body = { data: { itemDO: { itemId: "123" } } };
  const receiver = {};
  assert.equal(page.mtopjsonp1.call(receiver, body, "extra"), "page-result");
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], receiver);
  assert.deepEqual(calls[0][1], [body, "extra"]);
  assert.equal(page.__xianyuNativeCapture.entries.length, 1);
  assert.equal(page.__xianyuNativeCapture.entries[0].body.data.itemDO.itemId, "123");
  assert.equal(page.__xianyuNativeCapture.sequence, 1);
  const unchanged = page.mtopjsonp1;
  mutation([{ type: "childList", addedNodes: [{ ...script, src: "https://example.com/?callback=mtopjsonp1" }] }]);
  assert.equal(page.mtopjsonp1, unchanged);
});
