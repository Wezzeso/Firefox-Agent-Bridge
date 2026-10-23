(() => {
  "use strict";
  if (globalThis.__firefoxAgentBridgeV1) return;
  globalThis.__firefoxAgentBridgeV1 = true;
  const token = crypto.randomUUID();
  let sequence = 0;
  const refs = new Map();
  const ids = new WeakMap();
  const selector = "a[href],button,input,textarea,select,[role],[contenteditable='true'],[tabindex],summary";
  const trim = (s, n = 180) => String(s || "").replace(/\s+/g, " ").trim().slice(0, n);

  function visible(el) {
    if (!el.isConnected || !el.getClientRects().length || el.closest("[hidden],[inert]")) return false;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none" && style.opacity !== "0" && rect.width > 0 && rect.height > 0;
  }

  function elements() {
    const result = [];
    const roots = [document];
    let scanned = 0;
    while (roots.length && scanned < 12000) {
      for (const el of roots.shift().querySelectorAll("*")) {
        if (++scanned > 12000) break;
        if (el.shadowRoot) roots.push(el.shadowRoot);
        if (el.matches(selector) && visible(el)) result.push(el);
      }
    }
    return result;
  }

  function name(el) {
    const labelled = (el.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean)
      .map(id => el.getRootNode().getElementById?.(id)?.textContent || "").join(" ");
    const labels = Array.from(el.labels || []).map(label => {
      const copy = label.cloneNode(true);
      copy.querySelectorAll("input,textarea,select,button,[contenteditable]").forEach(control => control.remove());
      return copy.textContent;
    }).join(" ");
    return trim(el.getAttribute("aria-label") || labelled || labels ||
      el.getAttribute("alt") || el.getAttribute("placeholder") || el.innerText || el.getAttribute("title") ||
      (["submit", "button", "reset"].includes(el.type) ? el.value : ""));
  }

  function fingerprint(el) {
    return JSON.stringify([el.localName, el.getAttribute("role"), el.getAttribute("type"), name(el), el.getAttribute("href")]);
  }

  function describe(el) {
    let id = ids.get(el);
    if (!id) { id = `${token}:${++sequence}`; ids.set(el, id); }
    refs.set(id, { el, fingerprint: fingerprint(el) });
    const rect = el.getBoundingClientRect();
    const item = { elementId: id, tag: el.localName, role: el.getAttribute("role") || undefined,
      name: name(el), type: el.type || undefined, disabled: Boolean(el.disabled || el.getAttribute("aria-disabled") === "true"),
      inViewport: rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth,
      bounds: { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) } };
    if (el.matches("input,textarea,select")) item.value = el.type === "password" ? "[REDACTED]" : trim(el.value, 500);
    if (el.matches("input[type='checkbox'],input[type='radio']")) item.checked = el.checked;
    if (el.matches("select")) item.options = Array.from(el.options).slice(0, 100).map(o => ({ value: o.value, label: trim(o.label), selected: o.selected }));
    if (el.matches("a[href]")) item.href = el.href;
    return item;
  }

  function target(p, required = true) {
    if (p.elementId) {
      const ref = refs.get(p.elementId);
      if (!ref || !ref.el.isConnected) throw new Error("Элемент устарел. Получите новый snapshot.");
      if (fingerprint(ref.el) !== ref.fingerprint) throw new Error("Элемент изменился. Получите новый snapshot перед действием.");
      return ref.el;
    }
    if (p.selector) {
      if (typeof p.selector !== "string" || p.selector.length > 1000) throw new Error("Некорректный selector.");
      const matches = Array.from(document.querySelectorAll(p.selector)).filter(visible);
      if (matches.length > 1) throw new Error("Селектор неоднозначен: найдено несколько видимых элементов.");
      if (matches.length === 1) return matches[0];
    }
    if (!required) return null;
    throw new Error("Нужен elementId из snapshot или однозначный CSS selector.");
  }

  function actionable(el) {
    if (!visible(el)) throw new Error("Элемент не виден.");
    if (el.disabled || el.matches(":disabled") || el.getAttribute("aria-disabled") === "true") throw new Error("Элемент отключён.");
  }

  function uncovered(el) {
    actionable(el);
    el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
    const r = el.getBoundingClientRect();
    const x = (Math.max(0, r.left) + Math.min(innerWidth, r.right)) / 2;
    const y = (Math.max(0, r.top) + Math.min(innerHeight, r.bottom)) / 2;
    let top = document.elementFromPoint(x, y);
    while (top?.shadowRoot?.elementFromPoint) {
      const deeper = top.shadowRoot.elementFromPoint(x, y);
      if (!deeper || deeper === top) break;
      top = deeper;
    }
    let node = top;
    while (node && node !== el) node = node.parentNode || node.host;
    if (node !== el) throw new Error("Элемент перекрыт другим элементом. Обновите snapshot или закройте перекрытие.");
    return { x, y };
  }

  function setValue(el, value) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    if (!setter) throw new Error("Поле не поддерживает текстовый ввод.");
    setter.call(el, value);
  }

  function fill(el, text, append) {
    uncovered(el);
    if (typeof text !== "string" || text.length > 100000) throw new Error("text должен быть строкой до 100000 символов.");
    if (el.readOnly) throw new Error("Поле доступно только для чтения.");
    const input = el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement;
    if (input && ["file", "button", "submit", "reset", "checkbox", "radio", "image", "hidden"].includes(el.type)) throw new Error("Этот тип поля не поддерживает fill.");
    if (!input && !el.isContentEditable) throw new Error("Элемент не является текстовым полем.");
    el.focus();
    if (!el.dispatchEvent(new InputEvent("beforeinput", { bubbles: true, composed: true, cancelable: true,
      inputType: append ? "insertText" : "insertReplacementText", data: text }))) throw new Error("Сайт отменил ввод.");
    if (input) setValue(el, (append ? el.value : "") + text);
    else el.textContent = (append ? el.textContent : "") + text;
    el.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "insertText", data: text }));
    el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    return { filled: true, length: input ? el.value.length : el.textContent.length };
  }

  function act(action, p) {
    if (action === "snapshot") {
      const limit = p.maxElements ?? 300;
      const textLimit = p.maxText ?? 16000;
      if (!Number.isInteger(limit) || limit < 1 || limit > 1000 || !Number.isInteger(textLimit) || textLimit < 0 || textLimit > 60000) throw new Error("Некорректные лимиты snapshot.");
      refs.clear();
      const all = elements();
      // IDs only authorize elements present in this most recent snapshot.
      return { documentId: token, title: document.title, url: location.href, readyState: document.readyState,
        viewport: { width: innerWidth, height: innerHeight, scrollX, scrollY },
        text: (document.body?.innerText || "").slice(0, textLimit), elements: all.slice(0, limit).map(describe),
        truncated: all.length > limit, totalElements: all.length };
    }
    if (action === "probe") {
      const el = p.selector || p.elementId ? target(p, false) : null;
      const hasTarget = Boolean(p.selector || p.elementId);
      const textMatches = p.text === undefined || (typeof p.text === "string" && (document.body?.innerText || "").includes(p.text));
      return { matched: document.readyState !== "loading" && (!hasTarget || Boolean(el && visible(el))) && textMatches, readyState: document.readyState };
    }
    if (action === "scroll") {
      const x = p.x ?? 0, y = p.y ?? 600;
      if (![x, y].every(v => typeof v === "number" && Number.isFinite(v) && Math.abs(v) <= 100000)) throw new Error("Некорректные координаты прокрутки.");
      const el = p.elementId || p.selector ? target(p) : window;
      el.scrollBy({ left: x, top: y, behavior: "instant" });
      return { scrolled: true };
    }
    const el = target(p);
    actionable(el);
    if (action === "click") {
      uncovered(el);
      el.focus();
      el.click();
      return { clicked: true };
    }
    if (action === "fill") return fill(el, p.text, Boolean(p.append));
    if (action === "select") {
      if (!(el instanceof HTMLSelectElement)) throw new Error("Нужен элемент select.");
      uncovered(el);
      const values = Array.isArray(p.value) ? p.value : [p.value];
      if (!values.length || !values.every(v => typeof v === "string")) throw new Error("value: строка или непустой массив строк.");
      if (!el.multiple && values.length !== 1) throw new Error("select не поддерживает несколько значений.");
      for (const value of values) {
        const option = Array.from(el.options).find(o => o.value === value);
        if (!option || option.disabled || option.parentElement?.disabled) throw new Error(`Опция недоступна: ${value}`);
      }
      for (const option of el.options) option.selected = values.includes(option.value);
      el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
      el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
      return { selected: Array.from(el.selectedOptions).map(o => o.value) };
    }
    if (action === "check") {
      if (!el.matches("input[type='checkbox'],input[type='radio']") || typeof p.checked !== "boolean") throw new Error("Нужен checkbox/radio и boolean checked.");
      if (el.type === "radio" && !p.checked) throw new Error("Чтобы снять radio, выберите другой вариант группы.");
      if (el.checked !== p.checked) { uncovered(el); el.click(); }
      if (el.checked !== p.checked) throw new Error("Сайт не принял новое состояние.");
      return { checked: el.checked };
    }
    if (action === "press") {
      if (!["Enter", "Escape", "Tab"].includes(p.key)) throw new Error("press поддерживает Enter, Escape и Tab; события синтетические.");
      uncovered(el);
      el.focus();
      const init = { key: p.key, code: p.key, bubbles: true, composed: true, cancelable: true, shiftKey: Boolean(p.shift) };
      const proceed = el.dispatchEvent(new KeyboardEvent("keydown", init));
      if (proceed && p.key === "Enter") {
        if (el.matches("button,a[href],input[type='submit'],input[type='button'],summary")) { uncovered(el); el.click(); }
        else if (el instanceof HTMLInputElement && el.form) {
          const submitter = el.form.querySelector("button:not([type]),button[type='submit'],input[type='submit']");
          el.form.requestSubmit(submitter || undefined);
        }
      }
      if (proceed && p.key === "Tab") {
        const list = elements().filter(e => e.tabIndex >= 0 && !e.disabled);
        const index = list.indexOf(el);
        list[(index + (p.shift ? -1 : 1) + list.length) % list.length]?.focus();
      }
      el.dispatchEvent(new KeyboardEvent("keyup", init));
      return { pressed: p.key, synthetic: true };
    }
    throw new Error("Неизвестное действие страницы.");
  }

  browser.runtime.onMessage.addListener((message, sender) => {
    if (sender.id !== browser.runtime.id || message.channel !== "firefox-agent") return undefined;
    try { return Promise.resolve({ ok: true, result: act(message.action, message.params || {}) }); }
    catch (error) { return Promise.resolve({ ok: false, error: String(error.message || error) }); }
  });
})();
