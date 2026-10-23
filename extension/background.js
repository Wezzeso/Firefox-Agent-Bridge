"use strict";

const HOST = "local.firefox_agent_bridge";
const granted = new Set();
const audit = [];
let nativePort = null;
let connected = false;
let connectionError = "";
let retryTimer = null;
let queue = Promise.resolve();
let generation = 0;
let reconnectDelay = 1000;

function log(method, tabId, ok) {
  audit.unshift({ time: new Date().toISOString(), method, tabId, ok });
  audit.splice(20);
}

function badge() {
  browser.browserAction.setBadgeText({ text: granted.size ? String(granted.size) : "" });
  browser.browserAction.setBadgeBackgroundColor({ color: connected ? "#147d64" : "#a84329" });
}

function stopAll() {
  generation++;
  granted.clear();
  badge();
  return { stopped: true };
}

function httpUrl(raw) {
  if (typeof raw !== "string") throw new Error("Нужен URL в виде строки.");
  const url = new URL(raw);
  if (!["http:", "https:"].includes(url.protocol)) throw new Error("Разрешены только HTTP и HTTPS.");
  if (url.username || url.password) throw new Error("URL с паролем или логином не поддерживается.");
  return url.href;
}

function integer(value, name, min = 0) {
  if (!Number.isInteger(value) || value < min) throw new Error(`Некорректный ${name}.`);
  return value;
}

async function authorized(tabId, requireWeb = true) {
  integer(tabId, "tabId");
  if (!granted.has(tabId)) throw new Error("Нет доступа к вкладке. Включите управление кнопкой расширения.");
  const tab = await browser.tabs.get(tabId);
  if (!granted.has(tabId)) throw new Error("Управление остановлено.");
  if (tab.incognito) throw new Error("Приватные вкладки не поддерживаются.");
  if (requireWeb) httpUrl(tab.url);
  return tab;
}

async function content(tabId, frameId, action, params, valid) {
  await authorized(tabId);
  integer(frameId, "frameId");
  // Inject only the packaged script. No page-supplied code or eval.
  await browser.tabs.executeScript(tabId, { file: "/content.js", frameId, runAt: "document_idle" });
  await authorized(tabId);
  valid();
  const result = await browser.tabs.sendMessage(tabId,
    { channel: "firefox-agent", action, params }, { frameId });
  if (!result || !result.ok) throw new Error(result?.error || "Страница не ответила.");
  return result.result;
}

async function dispatch(method, p, valid) {
  valid();
  if (method === "bridge.status") return { connected, controlledTabs: granted.size, version: "1.1.0" };
  if (method === "bridge.stop") return stopAll();
  if (method === "tabs.list") {
    const tabs = await browser.tabs.query({});
    return tabs.filter(t => granted.has(t.id)).map(t => ({
      tabId: t.id, windowId: t.windowId, title: t.title, url: t.url, active: t.active, status: t.status
    }));
  }
  if (method === "tabs.create") {
    await authorized(p.sourceTabId);
    const url = httpUrl(p.url);
    valid();
    const tab = await browser.tabs.create({ url, active: p.active !== false, openerTabId: p.sourceTabId });
    try { valid(); } catch (error) { granted.delete(tab.id); throw error; }
    granted.add(tab.id);
    badge();
    return { tabId: tab.id, url: tab.url };
  }
  if (method === "page.wait") {
    integer(p.tabId, "tabId");
    const frameId = p.frameId ?? 0;
    const duration = p.timeoutMs ?? 8000;
    if (!Number.isInteger(duration) || duration < 0 || duration > 15000) throw new Error("timeoutMs: 0–15000.");
    const until = Date.now() + duration;
    let lastError;
    do {
      valid();
      if (!granted.has(p.tabId)) throw new Error("Нет доступа к вкладке. Управление остановлено.");
      try {
        const result = await content(p.tabId, frameId, "probe", p, valid);
        if (result.matched) return result;
      } catch (error) { lastError = error.message; }
      await new Promise(r => setTimeout(r, 150));
    } while (Date.now() < until);
    throw new Error(`Ожидание истекло.${lastError ? " " + lastError : ""}`);
  }
  const navigationOnly = new Set(["tabs.activate", "tabs.close", "page.navigate", "page.reload", "page.back", "page.forward"]);
  const tab = await authorized(p.tabId, !navigationOnly.has(method));
  valid();
  if (method === "tabs.activate") {
    await browser.tabs.update(tab.id, { active: true });
    await browser.windows.update(tab.windowId, { focused: true });
    return { activated: true };
  }
  if (method === "tabs.close") {
    await browser.tabs.remove(tab.id);
    return { closed: true };
  }
  if (method === "page.navigate") {
    const url = httpUrl(p.url);
    await browser.tabs.update(tab.id, { url });
    return { navigating: true, url };
  }
  if (["page.reload", "page.back", "page.forward"].includes(method)) {
    const fn = { "page.reload": "reload", "page.back": "goBack", "page.forward": "goForward" }[method];
    await browser.tabs[fn](tab.id);
    return { navigating: true };
  }
  if (method === "page.frames") {
    return (await browser.webNavigation.getAllFrames({ tabId: tab.id })).map(f => ({
      frameId: f.frameId, parentFrameId: f.parentFrameId, url: f.url
    }));
  }
  if (method === "page.screenshot") {
    await browser.tabs.update(tab.id, { active: true });
    const before = await authorized(tab.id);
    valid();
    // Firefox-specific API binds capture to an explicit tab ID, avoiding active-tab races.
    const dataUrl = await browser.tabs.captureTab(tab.id, { format: "jpeg", quality: 80 });
    const after = await authorized(tab.id);
    valid();
    if (after.url !== before.url) throw new Error("Вкладка изменилась во время снимка. Снимок отброшен.");
    if (dataUrl.length > 12 * 1024 * 1024) throw new Error("Скриншот слишком большой.");
    return { tabId: tab.id, mimeType: "image/jpeg", dataUrl };
  }
  const actions = new Set(["snapshot", "click", "fill", "select", "check", "press", "scroll", "wait"]);
  const action = method.startsWith("page.") ? method.slice(5) : "";
  if (!actions.has(action)) throw new Error(`Неизвестная команда: ${method}`);
  const frameId = p.frameId ?? 0;
  return content(tab.id, frameId, action, p, valid);
}

function connect() {
  if (nativePort) return;
  clearTimeout(retryTimer);
  connectionError = "";
  const port = browser.runtime.connectNative(HOST);
  nativePort = port;
  port.onMessage.addListener(message => {
    if (nativePort !== port) return;
    if (message.type === "ready") {
      connected = true;
      reconnectDelay = 1000;
      badge();
      return;
    }
    if (message.type !== "command") return;
    const epoch = generation;
    const valid = () => {
      if (epoch !== generation || port !== nativePort) throw new Error("Управление остановлено; выполните новую команду.");
      if (!Number.isFinite(message.deadline) || Date.now() > message.deadline) throw new Error("Команда просрочена; она не была начата.");
    };
    const execute = async () => {
      let response;
      try {
        const result = await dispatch(message.method, message.params || {}, valid);
        valid();
        response = { id: message.id, ok: true, result };
        log(message.method, message.params?.tabId, true);
      } catch (error) {
        response = { id: message.id, ok: false, error: String(error.message || error) };
        log(message.method, message.params?.tabId, false);
      }
      try { port.postMessage({ type: "response", ...response }); } catch (_) { /* disconnected */ }
    };
    // Emergency stop does not wait behind page loading or a wait command.
    if (message.method === "bridge.stop") {
      stopAll();
      port.postMessage({ type: "response", id: message.id, ok: true, result: { stopped: true } });
    } else {
      queue = queue.then(execute, execute);
    }
  });
  port.onDisconnect.addListener(() => {
    if (nativePort !== port) return;
    connectionError = port.error?.message || "Локальный мост отключён.";
    connected = false;
    nativePort = null;
    stopAll();
    retryTimer = setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(30000, reconnectDelay * 2);
  });
  port.postMessage({ type: "hello", version: "1.1.0" });
}

browser.runtime.onMessage.addListener(async (message, sender) => {
  // Only our packaged popup can grant access. Content scripts and websites cannot.
  if (sender.id !== browser.runtime.id || sender.url !== browser.runtime.getURL("popup.html")) return undefined;
  try {
    const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
    if (message.action === "grant") {
      if (!tab || tab.incognito) throw new Error("Откройте обычную вкладку HTTP/HTTPS.");
      httpUrl(tab.url);
      granted.add(tab.id);
      badge();
    } else if (message.action === "revoke") {
      generation++;
      granted.delete(tab?.id);
      badge();
    } else if (message.action === "stop") {
      stopAll();
    } else if (message.action === "reconnect") {
      if (nativePort) nativePort.disconnect();
      nativePort = null;
      connected = false;
      stopAll();
      connect();
    } else if (message.action !== "status") {
      throw new Error("Неизвестное действие.");
    }
    return { ok: true, connected, connectionError, granted: granted.has(tab?.id), count: granted.size,
      tabTitle: tab?.title || "Нет вкладки", audit };
  } catch (error) { return { ok: false, error: error.message }; }
});

browser.tabs.onRemoved.addListener(tabId => { granted.delete(tabId); badge(); });
browser.tabs.onCreated.addListener(tab => {
  if (!tab.incognito && granted.has(tab.openerTabId)) { granted.add(tab.id); badge(); }
});
browser.commands.onCommand.addListener(command => { if (command === "stop-all") stopAll(); });
connect();
