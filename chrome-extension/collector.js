function text(value, limit = 30000) {
  return value == null ? "" : String(value).slice(0, limit);
}

function images(values) {
  return [...new Set((values || []).map(value => {
    const url = text(typeof value === "object" ? value.url : value);
    return url.startsWith("//") ? "https:" + url : url;
  }).filter(url => {
    try {
      const parsed = new URL(url);
      return parsed.protocol === "https:" && !parsed.username && !parsed.password &&
        (!parsed.port || parsed.port === "443") &&
        /(^|\.)(alicdn\.com|goofish\.com|taobaocdn\.com|tbcdn\.cn)$/.test(parsed.hostname);
    } catch (_) { return false; }
  }))].slice(0, 30);
}

export function parseSearch(body) {
  return (body?.data?.resultList || []).flatMap(entry => {
    const main = entry?.data?.item?.main;
    const data = main?.exContent;
    if (!data || !/^\d+$/.test(String(data.itemId))) return [];
    const params = main.clickParam?.args || {};
    const tags = Object.values(data.fishTags || {}).flatMap(group =>
      (group.tagList || []).map(tag => text(tag.data?.content, 100)));
    if (params.tag === "freeship" && !tags.includes("包邮")) tags.push("包邮");
    const timestamp = Number(params.publishTime);
    return [{
      id: String(data.itemId), title: text(data.title, 2000),
      price: text(Array.isArray(data.price) ? data.price.map(part => part.text || "").join("") : data.price, 50),
      original_price: text(data.oriPrice, 50), area: text(data.area, 200),
      seller_nickname: text(data.userNickName, 200), images: images([data.picUrl]),
      tags, wants: text(params.wantNum, 50),
      personal: tags.includes("个人闲置") ? true : null,
      publish_time: timestamp > 0 ? new Date(timestamp).toISOString() : "",
    }];
  });
}

export function parseDetail(body, id) {
  const item = body?.data?.itemDO;
  const seller = body?.data?.sellerDO || {};
  if (!item || String(item.itemId || item.id) !== id) return null;
  return {
    id, title: text(item.title, 2000), description: text(item.desc || item.description),
    price: text(item.soldPrice || item.price, 50), area: text(item.area || item.location, 200),
    images: images(item.imageInfos), wants: text(item.wantCnt, 50), views: text(item.browseCnt, 50),
    seller_id: /^\d+$/.test(String(seller.sellerId)) ? String(seller.sellerId) : "",
    seller_nickname: text(seller.nick || seller.nickName || seller.userNick, 200),
    seller_credit: text(seller.zhimaLevelInfo?.levelName, 200),
    seller_registration: seller.userRegDay == null ? "" : text(seller.userRegDay, 100) + " 天",
    personal: typeof seller.isBiz === "boolean" ? !seller.isBiz : null,
  };
}

export function parseSeller(entries) {
  const heads = entries.filter(entry => entry.url.includes("mtop.idle.web.user.page.head"));
  const base = heads.at(-1)?.body?.data?.module?.base || {};
  const tabs = heads.at(-1)?.body?.data?.module?.tabs || {};
  const credits = base.ylzTags || [];
  const itemCards = entries.filter(entry => entry.url.includes("mtop.idle.web.xyh.item.list"))
    .flatMap(entry => entry.body?.data?.cardList || []);
  const ratings = entries.filter(entry => entry.url.includes("mtop.idle.web.trade.rate.list"))
    .flatMap(entry => entry.body?.data?.cardList || []).map(card => card.cardData || {});
  const unique = (items, key) => [...new Map(items.map(item => [item[key], item])).values()];
  const rates = unique(ratings, "rateId");
  const counts = role => {
    const entries = rates.filter(rate => (rate.rateTagList || []).some(tag => text(tag.text).includes(role)));
    const positive = entries.filter(rate => Number(rate.rate) === 1).length;
    return [positive + "/" + entries.length, entries.length ? (positive / entries.length * 100).toFixed(2) + "%" : "N/A"];
  };
  const sellerRate = counts("卖家");
  const buyerRate = counts("买家");
  return {
    "卖家昵称": text(base.displayName, 200), "卖家个性签名": text(base.introduction, 2000),
    "卖家在售/已售商品数": text(tabs.item?.number, 100), "卖家收到的评价总数": text(tabs.rate?.number, 100),
    "卖家信用等级": text(credits.find(tag => tag.attributes?.role === "seller")?.text, 200),
    "买家信用等级": text(credits.find(tag => tag.attributes?.role === "buyer")?.text, 200),
    "卖家发布的商品列表": unique(itemCards.map(card => card.cardData || card), "id").slice(0, 500).map(item => ({
      "商品ID": text(item.id), "商品标题": text(item.title, 2000),
      "商品价格": text(item.priceInfo?.price, 50), "商品主图": images([item.picInfo?.picUrl])[0] || "",
      "商品状态": Number(item.itemStatus) === 0 ? "在售" : Number(item.itemStatus) === 1 ? "已售" : "未知",
    })),
    "卖家收到的评价列表": rates.slice(0, 500).map(rate => ({
      "评价ID": text(rate.rateId), "评价内容": text(rate.feedback, 3000),
      "评价类型": Number(rate.rate) === 1 ? "好评" : Number(rate.rate) === -1 ? "差评" : "中评",
      "评价来源角色": text(rate.rateTagList?.[0]?.text, 100), "评价时间": text(rate.gmtCreate),
    })),
    "作为卖家的好评数": sellerRate[0], "作为卖家的好评率": sellerRate[1],
    "作为买家的好评数": buyerRate[0], "作为买家的好评率": buyerRate[1],
    "采集范围": "当前页面已加载的商品和评价",
  };
}

// This function is serialized into the page, so it must not reference module scope.
export function readPage(action) {
  const entries = window.__xianyuNativeCapture?.entries || [];
  const content = document.body?.innerText || "";
  const visible = element => element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden';
  const latest = new Map(entries.map(entry => [new URL(entry.url, location.href).pathname, entry]));
  const barrier = /passport\.goofish|mini_login/.test(location.href) ||
    [...document.querySelectorAll('iframe[src*="baxia"], #baxia-dialog-mask, [id*="nc_"][id$="_wrapper"]')].some(visible) ||
    [...latest.values()].some(entry => (entry.body.ret || []).some(value => /FAIL_SYS_USER_VALIDATE|SESSION_EXPIRED|TOKEN_EXPIRED|FAIL_SYS_LOGIN/.test(value)));
  const login = [...document.querySelectorAll('[role="dialog"]')].some(dialog => visible(dialog) && /扫码登录|请先登录|安全验证|滑动验证/.test(dialog.innerText));
  if (barrier || login) return { href: location.href, sequence: window.__xianyuNativeCapture?.sequence || 0, attention: "请在 Chrome 页面中完成登录或安全验证，页面恢复后将自动继续。" };
  const state = { entries, sequence: window.__xianyuNativeCapture?.sequence || 0,
    href: location.href, ready: document.readyState === "complete", content: content.slice(0, 30000),
    heapBytes: performance.memory?.usedJSHeapSize || 0 };
  if (action === "search" || action === "search_next") {
    const activePage = document.querySelector('[class*="search-pagination-page-box-active"], .ant-pagination-item-active, [aria-current="page"]');
    state.currentPage = Number(activePage?.textContent.trim()) ||
      Number(document.querySelector('[class*="search-page-tiny-page"]')?.textContent.split('/')[0].trim()) || null;
    state.domItems = [...document.querySelectorAll('a[href*="/item?"]')].flatMap(link => {
      let url;
      try { url = new URL(link.href); } catch (_) { return []; }
      const id = url.searchParams.get("id");
      if (!id || !/^\d+$/.test(id)) return [];
      const card = link.closest('[class*="feeds-item"], [class*="card"], [class*="item-container"]') || link;
      const lines = (card.innerText || "").split("\n").map(line => line.trim()).filter(Boolean);
      const title = card.querySelector('[class*="title"]')?.textContent || link.title || link.querySelector("img")?.alt || lines.find(line => !/^[¥￥\d]/.test(line));
      const price = (card.innerText || "").match(/[¥￥]\s*[\d,.]+(?:万)?/);
      if (!title || !price) return [];
      const img = card.querySelector("img");
      return [{
        id, title: title.slice(0, 2000), price: price[0], images: img?.src ? [img.src] : [],
        area: card.querySelector('[class*="area"], [class*="location"]')?.textContent?.trim() || "",
        seller_nickname: card.querySelector('[class*="nick"], [class*="user-name"]')?.textContent?.trim() || "",
        tags: /包邮/.test(card.innerText) ? ["包邮"] : [],
        personal: /个人闲置/.test(card.innerText) ? true : null,
      }];
    });
    const next = document.querySelector('[class*="search-pagination-arrow-right"], [class*="search-page-tiny-arrow-right"]')?.closest('button') ||
      document.querySelector('[title="下一页"], [aria-label="下一页"], .ant-pagination-next') ||
      [...document.querySelectorAll('button, a, li')].find(element => element.textContent.trim() === "下一页");
    state.hasMore = !!next && !next.hasAttribute("disabled") && next.getAttribute("aria-disabled") !== "true" && !/disabled/.test(next.className);
    state.empty = /暂无搜索结果|没有找到相关|没有找到符合|暂无商品/.test(content);
  } else if (action === "detail") {
    const id = new URL(location.href).searchParams.get("id") || "";
    const title = document.querySelector('[class*="title"], h1')?.textContent?.trim() || "";
    const description = document.querySelector('[class*="desc"], [class*="content-text"]')?.textContent?.trim() || "";
    const price = content.match(/[¥￥]\s*[\d,.]+(?:万)?/);
    const sellerLink = document.querySelector('a[href*="/personal?userId="]');
    state.domItem = title && price && id ? {
      id, title: title.slice(0, 2000), description: description.slice(0, 30000),
      price: price[0], seller_id: sellerLink ? new URL(sellerLink.href).searchParams.get("userId") || "" : "",
      seller_nickname: sellerLink?.innerText?.slice(0, 200) || "",
      images: [...document.querySelectorAll('[class*="image"] img, [class*="slider"] img')].map(img => img.src).slice(0, 30),
      tags: /包邮/.test(content) ? ["包邮"] : [],
    } : null;
  }
  return state;
}

export async function interactPage(operation, options = {}) {
  const settle = () => new Promise(resolve => setTimeout(resolve, 250));
  let changed = false;
  let sequenceFloor = window.__xianyuNativeCapture?.sequence || 0;
  const elements = () => [...document.querySelectorAll('button, a, li, label, [role="checkbox"], [role="button"], [role="option"], span, div')];
  const visible = element => element.getClientRects().length && getComputedStyle(element).visibility !== "hidden";
  // The site's custom dropdown uses divs. Prefer the innermost text match so
  // an enclosing panel with the same text is never clicked instead of its item.
  const exact = label => elements().filter(element => element.textContent.trim() === label && visible(element))
    .find(element => ![...element.children].some(child => child.textContent.trim() === label));
  const selected = element => [element, element.parentElement, element.closest("label")].filter(Boolean).some(node =>
    node.getAttribute("aria-checked") === "true" || node.getAttribute("aria-selected") === "true" || node.querySelector('input:checked') ||
    node.querySelector('[class*="search-checkbox-checked--"]') ||
    /(^|[-_ ])(checked|selected|active)([-_ ]|$)/i.test(node.className));
  const clickFilter = element => {
    // Only responses after the last filter change can represent all filters.
    sequenceFloor = window.__xianyuNativeCapture?.sequence || 0;
    element.click();
    changed = true;
  };
  if (operation === "next") {
    const next = document.querySelector('[class*="search-pagination-arrow-right"], [class*="search-page-tiny-arrow-right"]')?.closest('button') ||
      document.querySelector('[title="下一页"], [aria-label="下一页"], .ant-pagination-next') || exact("下一页");
    if (!next || next.hasAttribute("disabled") || next.getAttribute("aria-disabled") === "true" || /disabled/.test(next.className)) return { clicked: false };
    if (window.__xianyuNativeCapture) window.__xianyuNativeCapture.entries = [];
    next.click();
    return { clicked: true, sequenceFloor };
  }
  if (operation === "filters") {
    const personal = exact("个人闲置") || exact("个人卖家");
    if (options.personal_only && personal && !selected(personal)) { clickFilter(personal); await settle(); }
    const freeShipping = exact("包邮");
    if (options.free_shipping && freeShipping && !selected(freeShipping)) { clickFilter(freeShipping); await settle(); }
    if (options.new_publish_option) {
      const label = options.new_publish_option;
      const retry = error => ({ retry: true, error: "发布时间筛选「" + label + "」" + error, changed, sequenceFloor });
      const pending = () => ({ pending: true, changed, sequenceFloor, error: "发布时间筛选「" + label + "」尚未生效。" });
      // Goofish renders the options even while its CSS :hover menu is hidden.
      // Clicking the existing item invokes the site's normal React handler;
      // synthetic mouse events cannot activate CSS :hover in a minimized tab.
      const dropdown = [...document.querySelectorAll('[class*="search-select-container"]')].find(node => {
        const menu = node.querySelector('[class*="search-select-items-container"]');
        const labels = menu ? [...menu.children].map(item => item.textContent.trim()) : [];
        return visible(node) && labels.includes("最新") && labels.includes("1天内") && labels.includes("3天内");
      });
      const title = dropdown?.querySelector('[class*="search-select-title--"]');
      const menu = dropdown?.querySelector('[class*="search-select-items-container"]');
      const option = menu ? [...menu.children].find(item => item.textContent.trim() === label) : exact(label);
      const applied = () => {
        if (title) return title.textContent.trim() === label;
        if (option && selected(option)) return true;
        const clicked = window.__xianyuNativePublishFilter;
        return clicked?.label === label && clicked.stage === "clicked" && (window.__xianyuNativeCapture?.entries || []).some(entry =>
          entry.url.includes("idlemtopsearch.pc.search") && entry.sequence > clicked.sequenceFloor &&
          entry.body?.ret?.some(value => value.startsWith("SUCCESS")));
      };
      const previous = window.__xianyuNativePublishFilter;
      if (applied()) {
        delete window.__xianyuNativePublishFilter;
      } else if (previous?.label === label && previous.stage === "clicked") {
        // Yield to the service worker between observations so background page
        // timers cannot hold its poll loop and heartbeat for minutes.
        if (Date.now() - previous.clickedAt < 15000) return pending();
        delete window.__xianyuNativePublishFilter;
        return retry("未确认筛选生效。");
      } else if (option) {
        clickFilter(option);
        window.__xianyuNativePublishFilter = { label, stage: "clicked", clickedAt: Date.now(), sequenceFloor };
        if (!applied()) return pending();
        delete window.__xianyuNativePublishFilter;
      } else {
        const trigger = exact("新发布");
        if (!trigger) return retry("未找到菜单入口。");
        if (previous?.label !== label || previous.stage !== "opening") {
          trigger.click();
          for (const node of [trigger, trigger.parentElement].filter(Boolean)) {
            node.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
          }
          window.__xianyuNativePublishFilter = { label, stage: "opening", clickedAt: Date.now() };
          return pending();
        }
        if (Date.now() - previous.clickedAt < 15000) return pending();
        delete window.__xianyuNativePublishFilter;
        return retry("未找到菜单选项。");
      }
    }
    if ((options.min_price != null && String(options.min_price).trim() !== "" && Number(options.min_price) !== 0) ||
        (options.max_price != null && String(options.max_price).trim() !== "")) {
      const retry = error => ({ retry: true, error: "页面价格筛选" + error, changed, sequenceFloor });
      const pending = () => ({ pending: true, error: "页面价格筛选尚未生效。", changed, sequenceFloor });
      const amount = value => value == null || String(value).trim() === "" ? "" : String(Number(value));
      const minimum = amount(options.min_price);
      const maximum = amount(options.max_price);
      if ([minimum, maximum].some(value => value !== "" && (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value))))) {
        return retry("只支持非负整数金额。");
      }
      if (maximum === "0" || minimum !== "" && maximum !== "" && Number(minimum) > Number(maximum)) {
        return retry("区间无效，请检查任务价格设置。");
      }
      const desired = [minimum === "0" ? "" : minimum, maximum];
      const signature = JSON.stringify(desired);
      const container = [...document.querySelectorAll('[class*="search-price-input-container"]')].find(visible);
      const inputs = container ? [...container.querySelectorAll('input[placeholder="¥"], input[placeholder="￥"]')] : [];
      const confirm = container?.querySelector('[class*="search-price-confirm-button"]') ||
        [...(container?.querySelectorAll('button') || [])].find(node => node.textContent.trim() === "确定");
      if (inputs.length !== 2 || !confirm) return retry("未找到输入框或提交按钮。");
      let previous = window.__xianyuNativePriceFilter;
      if (previous?.signature !== signature) previous = null;
      const valuesMatch = inputs.every((input, index) => input.value === desired[index]);
      if (previous?.stage === "submitted") {
        const received = (window.__xianyuNativeCapture?.entries || []).some(entry => {
          if (!entry.url.includes("idlemtopsearch.pc.search") || entry.sequence <= previous.sequenceFloor ||
              !entry.body?.ret?.some(value => value.startsWith("SUCCESS"))) return false;
          try {
            const data = new URL(entry.url, location.href).searchParams.get("data");
            if (!data) return true;
            const request = JSON.parse(data);
            const properties = typeof request.propValueStr === "string" ? JSON.parse(request.propValueStr) : request.propValueStr;
            const range = (properties?.searchFilter || "").match(/(?:^|;)priceRange:([^,;]*),([^;]*);/);
            return !!range && range[1] === (desired[0] || "0") &&
              range[2] === (desired[1] || "undefined");
          } catch (_) { return false; }
        });
        if (received && valuesMatch) {
          previous.stage = "applied";
        } else if (Date.now() - previous.submittedAt < 15000) {
          return pending();
        } else {
          delete window.__xianyuNativePriceFilter;
          return retry("未收到对应区间的搜索结果。");
        }
      }
      if (previous?.stage !== "applied" || !valuesMatch) {
        if (previous?.stage === "filled" && valuesMatch && !confirm.disabled) {
          clickFilter(confirm);
          window.__xianyuNativePriceFilter = { signature, stage: "submitted", submittedAt: Date.now(), sequenceFloor };
          return pending();
        }
        if (previous?.stage === "filled") {
          if (Date.now() - previous.filledAt < 15000) return pending();
          delete window.__xianyuNativePriceFilter;
          return retry("输入值未被页面接受。");
        }
        // Bypass React's instance value tracker and send its normal input event.
        // Submit on the next worker tick after the controlled state has committed.
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
        inputs.forEach((input, index) => {
          if (input.value === desired[index]) return;
          setter.call(input, desired[index]);
          input.dispatchEvent(new Event("input", { bubbles: true }));
        });
        window.__xianyuNativePriceFilter = { signature, stage: "filled", filledAt: Date.now() };
        return pending();
      }
    }
    let regionApplied = false;
    if (options.region) {
      const trigger = exact("区域");
      if (!trigger) return { attention: "未找到区域筛选，请检查页面结构。" };
      trigger.click();
      await settle();
      for (const part of options.region.split("/").map(part => part.trim()).filter(Boolean)) {
        const option = [...document.querySelectorAll('[class*="areaWrap"] div, [class*="AreaWrap"] div, [class*="filterPanel"] div')]
          .find(element => element.textContent.trim() === part && element.getClientRects().length);
        if (!option) return { attention: "区域筛选中未找到「" + part + "」，请检查任务区域设置。" };
        option.click();
        await settle();
      }
      const apply = elements().find(element => /^查看\d+件宝贝$/.test(element.textContent.trim()) && element.getClientRects().length);
      if (!apply) return { attention: "未找到区域筛选的提交按钮，请检查页面。" };
      clickFilter(apply);
      await settle();
      regionApplied = true;
    }
    return { personalApplied: !!options.personal_only && !!personal && !!selected(personal), regionApplied, changed, sequenceFloor };
  }
  if (operation === "ratings") {
    const rating = exact("信用及评价") || exact("信用评价");
    if (rating) rating.click();
    return { clicked: !!rating };
  }
  if (operation === "scroll") {
    window.scrollTo(0, document.body.scrollHeight);
    return { scrolled: true };
  }
  return {};
}

export function collectSnapshot(snapshot, command) {
  if (snapshot.attention) return { outcome: "attention", message: snapshot.attention };
  const entries = snapshot.entries || [];
  if (command.action === "search" || command.action === "search_next") {
    const responses = entries.filter(entry => entry.url.includes("idlemtopsearch.pc.search"));
    const body = responses.at(-1)?.body;
    const items = body ? parseSearch(body) : (snapshot.domItems || []).map(item => ({ ...item, images: images(item.images) }));
    if (!items.length && !body && !snapshot.empty) return null;
    if (body && Array.isArray(body.ret) && body.ret.some(value => !value.startsWith("SUCCESS"))) {
      return { outcome: "attention", message: "搜索请求未成功：" + body.ret.join("；").slice(0, 1000) };
    }
    return { outcome: "ok", items, has_more: snapshot.hasMore, personal_applied: command.task?.personal_only ? snapshot.personalApplied : false };
  }
  if (command.action === "detail") {
    const id = new URL(command.url).searchParams.get("id");
    const detail = entries.filter(entry => entry.url.includes("idle.pc.detail")).map(entry => parseDetail(entry.body, id)).find(Boolean);
    const item = detail || snapshot.domItem;
    if (!item) return null;
    return { outcome: "ok", item: { ...item, images: images(item.images) } };
  }
  if (command.action === "seller") {
    if (!entries.some(entry => entry.url.includes("mtop.idle.web.user.page.head"))) return null;
    return { outcome: "ok", seller: parseSeller(entries) };
  }
  return null;
}
