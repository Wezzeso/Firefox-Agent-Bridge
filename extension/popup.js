"use strict";
const $ = id => document.getElementById(id);
let busy = false;
async function update(action = "status") {
  if (busy) return;
  busy = true;
  try {
    const state = await browser.runtime.sendMessage({ action });
    if (!state?.ok) throw new Error(state?.error || "Расширение не ответило.");
    $("dot").classList.toggle("online", state.connected);
    $("connection").textContent = state.connected ? "Локальный мост подключён" : "Мост не подключён";
    $("tab").textContent = state.tabTitle;
    $("permission").textContent = state.granted ? "Управление разрешено" : "Управление выключено";
    $("grant").hidden = state.granted;
    $("grant").disabled = !state.connected;
    $("revoke").hidden = !state.granted;
    $("count").textContent = `Вкладок под управлением: ${state.count}`;
    $("error").hidden = state.connected;
    $("error").textContent = state.connected ? "" : `${state.connectionError || "Ожидание подключения."} Выполните python3 install.py из папки расширения.`;
    $("audit").replaceChildren(...state.audit.map(item => {
      const li = document.createElement("li");
      li.textContent = `${new Date(item.time).toLocaleTimeString()} · ${item.method} · ${item.ok ? "OK" : "ошибка"}`;
      return li;
    }));
  } catch (error) { $("error").hidden = false; $("error").textContent = error.message; }
  finally { busy = false; }
}
for (const action of ["grant", "revoke", "stop", "reconnect"]) $(action).addEventListener("click", () => update(action));
update();
setInterval(() => update(), 1000);
