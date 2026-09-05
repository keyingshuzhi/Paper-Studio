(() => {
  "use strict";

  const pageMeta = {
    research: "研究工作台",
    compare: "对比研究",
    jobs: "任务中心",
    schedules: "定时计划",
    reports: "研究报告",
    library: "本地文献库",
    memory: "知识记忆",
    skills: "能力中心",
    settings: "设置"
  };
  const body = document.body;
  const nav = document.querySelector(".tabs");
  const tabs = [...document.querySelectorAll(".tab[data-p]")];
  const location = document.getElementById("workspaceLocation");
  const mobileToggle = document.getElementById("mobileNavToggle");
  const collapseToggle = document.getElementById("navToggle");
  const providerBadge = document.getElementById("providerBadge");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const bootScreen = document.getElementById("appBoot");
  const bootStartedAt = Number(window.__paperStudioBootStartedAt) || performance.now();
  let bootDismissQueued = false;

  function dismissBootScreen() {
    if (!bootScreen || bootDismissQueued) return;
    bootDismissQueued = true;
    const minimumVisibleTime = reduceMotion.matches ? 0 : 900;
    const delay = Math.max(0, minimumVisibleTime - (performance.now() - bootStartedAt));
    window.setTimeout(() => {
      window.clearTimeout(window.__paperStudioBootFallback);
      body.classList.remove("booting");
      body.setAttribute("aria-busy", "false");
      bootScreen.classList.add("is-leaving");
      bootScreen.setAttribute("aria-hidden", "true");
      window.dispatchEvent(new CustomEvent("paperstudio:ready"));
      window.setTimeout(() => bootScreen.remove(), reduceMotion.matches ? 0 : 480);
    }, delay);
  }

  if (!nav || !tabs.length) {
    dismissBootScreen();
    return;
  }
  nav.setAttribute("role", "tablist");
  nav.setAttribute("aria-orientation", "vertical");

  const scrim = document.createElement("div");
  scrim.className = "nav-scrim";
  scrim.setAttribute("aria-hidden", "true");
  document.body.appendChild(scrim);

  function setMobileNav(open) {
    const mobile = window.innerWidth <= 900;
    const expanded = mobile && open;
    body.classList.toggle("nav-mobile-open", expanded);
    mobileToggle?.setAttribute("aria-expanded", String(expanded));
    mobileToggle?.setAttribute("aria-label", expanded ? "关闭导航" : "打开导航");
    if (mobile) {
      nav.toggleAttribute("inert", !expanded);
      nav.setAttribute("aria-hidden", String(!expanded));
    } else {
      nav.removeAttribute("inert");
      nav.removeAttribute("aria-hidden");
    }
  }

  function syncActiveTab(active, { scroll = false } = {}) {
    if (!active) return;
    const page = active.dataset.p;
    tabs.forEach(tab => {
      const selected = tab === active;
      tab.classList.toggle("on", selected);
      tab.setAttribute("aria-selected", String(selected));
      if (selected) tab.setAttribute("aria-current", "page");
      else tab.removeAttribute("aria-current");
      tab.tabIndex = selected ? 0 : -1;
      const pane = document.getElementById(`p-${tab.dataset.p}`);
      if (pane) {
        pane.setAttribute("aria-hidden", String(!selected));
        pane.setAttribute("tabindex", "-1");
      }
    });
    body.dataset.page = page;
    if (location) location.textContent = pageMeta[page] || active.textContent.trim();
    const skip = document.querySelector(".skip-link");
    if (skip) skip.setAttribute("href", `#p-${page}`);
    document.title = `${pageMeta[page] || "Paper Studio"} · Paper Studio`;
    if (scroll) {
      window.scrollTo({ top: 0, behavior: reduceMotion.matches ? "auto" : "smooth" });
    }
  }

  tabs.forEach((tab, index) => {
    const page = tab.dataset.p;
    const id = `nav-${page}`;
    tab.id = id;
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", `p-${page}`);
    tab.title = pageMeta[page] || tab.textContent.trim();
    const pane = document.getElementById(`p-${page}`);
    if (pane) {
      pane.setAttribute("role", "tabpanel");
      pane.setAttribute("aria-labelledby", id);
    }
    tab.addEventListener("click", () => {
      requestAnimationFrame(() => syncActiveTab(tab, { scroll: true }));
      setMobileNav(false);
    });
    tab.addEventListener("keydown", event => {
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      let next = index;
      if (event.key === "ArrowDown") next = (index + 1) % tabs.length;
      if (event.key === "ArrowUp") next = (index - 1 + tabs.length) % tabs.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = tabs.length - 1;
      tabs[next].click();
      tabs[next].focus({ preventScroll: true });
    });
  });

  mobileToggle?.addEventListener("click", () => {
    setMobileNav(!body.classList.contains("nav-mobile-open"));
  });
  scrim.addEventListener("click", () => setMobileNav(false));
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") setMobileNav(false);
  });
  window.addEventListener("resize", () => {
    setMobileNav(false);
  });

  try {
    if (localStorage.getItem("paper-studio-nav-collapsed") === "1" && window.innerWidth > 900) {
      body.classList.add("nav-collapsed");
    }
  } catch (_) {}

  function syncCollapseLabel() {
    if (!collapseToggle) return;
    const collapsed = body.classList.contains("nav-collapsed");
    collapseToggle.textContent = collapsed ? "展开侧栏" : "收起侧栏";
    collapseToggle.title = collapsed ? "展开导航" : "收起导航";
    collapseToggle.setAttribute("aria-expanded", String(!collapsed));
  }
  syncCollapseLabel();
  collapseToggle?.addEventListener("click", () => {
    requestAnimationFrame(() => {
      syncCollapseLabel();
      try {
        localStorage.setItem("paper-studio-nav-collapsed", body.classList.contains("nav-collapsed") ? "1" : "0");
      } catch (_) {}
    });
  });

  providerBadge?.addEventListener("click", () => {
    document.querySelector('.tab[data-p="settings"]')?.click();
    requestAnimationFrame(() => document.querySelector('.settings-nav [data-setting="models"]')?.click());
  });

  const settingsNav = document.querySelector(".settings-nav");
  const settingsTabs = [...document.querySelectorAll(".settings-nav [data-setting]")];
  if (settingsNav && settingsTabs.length) {
    settingsNav.setAttribute("role", "tablist");
    const syncSettingsOrientation = () => settingsNav.setAttribute("aria-orientation", window.innerWidth <= 980 ? "horizontal" : "vertical");
    syncSettingsOrientation();
    window.addEventListener("resize", syncSettingsOrientation);
    settingsTabs.forEach((tab, index) => {
      const panel = document.getElementById(`setting-${tab.dataset.setting}`);
      const tabId = `settings-tab-${tab.dataset.setting}`;
      tab.id = tabId;
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-controls", panel?.id || "");
      if (panel) {
        panel.setAttribute("role", "tabpanel");
        panel.setAttribute("aria-labelledby", tabId);
      }
      const sync = () => {
        settingsTabs.forEach(item => {
          const selected = item === tab;
          item.setAttribute("aria-selected", String(selected));
          item.tabIndex = selected ? 0 : -1;
        });
        tab.scrollIntoView({ block: "nearest", inline: "nearest", behavior: reduceMotion.matches ? "auto" : "smooth" });
      };
      tab.addEventListener("click", () => requestAnimationFrame(sync));
      tab.addEventListener("keydown", event => {
        if (!['ArrowDown', 'ArrowUp', 'ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        let next = index;
        if (event.key === 'ArrowDown' || event.key === 'ArrowRight') next = (index + 1) % settingsTabs.length;
        if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') next = (index - 1 + settingsTabs.length) % settingsTabs.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = settingsTabs.length - 1;
        settingsTabs[next].click();
        settingsTabs[next].focus({ preventScroll: true });
      });
    });
    const current = settingsTabs.find(tab => tab.classList.contains("on")) || settingsTabs[0];
    settingsTabs.forEach(tab => {
      const selected = tab === current;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
    });
  }
  if (providerBadge) {
    const observer = new MutationObserver(() => {
      providerBadge.setAttribute("aria-label", `模型连接状态：${providerBadge.textContent.trim()}。点击打开模型设置`);
    });
    observer.observe(providerBadge, { childList: true, subtree: true, characterData: true });
  }

  const reportExportMenu = document.querySelector(".report-export-menu");
  reportExportMenu?.addEventListener("click", event => {
    if (event.target.closest("button")) requestAnimationFrame(() => reportExportMenu.removeAttribute("open"));
  });
  document.addEventListener("click", event => {
    if (reportExportMenu?.open && !reportExportMenu.contains(event.target)) reportExportMenu.removeAttribute("open");
  });

  // Action menus stay outside the card's layout and close without moving it.
  const menus = () => [...document.querySelectorAll(".workspace-menu, .report-export-menu")];
  document.addEventListener("click", event => {
    const disabledSummary = event.target.closest('summary[aria-disabled="true"]');
    if (disabledSummary) { event.preventDefault(); return; }
    menus().forEach(menu => {
      if (!menu.contains(event.target) || event.target.closest("button")) menu.open = false;
    });
    const popover = event.target.closest(".action-popover");
    if (popover && event.target.closest("button:not(:disabled)")) popover.hidePopover();
  });
  document.addEventListener("toggle", event => {
    if (!(event.target instanceof Element)) return;
    const menu = event.target;
    if (menu.matches(".workspace-menu, .report-export-menu") && menu.open) {
      menus().forEach(other => { if (other !== menu) other.open = false; });
    }
    if (!menu.matches(".action-popover") || event.newState !== "open") return;
    const trigger = menu.parentElement.querySelector("[popovertarget]");
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const width = menu.offsetWidth, height = menu.offsetHeight;
    const left = Math.min(window.innerWidth - width - 12, Math.max(12, rect.right - width));
    const below = rect.bottom + 6;
    const top = below + height <= window.innerHeight - 12 ? below : Math.max(12, rect.top - height - 6);
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
  }, true);
  const closePopovers = event => {
    document.querySelectorAll(".action-popover:popover-open").forEach(popover => {
      if (!event || !(event.target instanceof Node) || !popover.contains(event.target)) popover.hidePopover();
    });
  };
  document.addEventListener("scroll", closePopovers, true);
  window.addEventListener("resize", closePopovers);
  tabs.forEach(tab => tab.addEventListener("click", () => {
    closePopovers(); menus().forEach(menu => { menu.open = false; });
  }));
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    const open = menus().find(menu => menu.open);
    if (open) { open.open = false; open.querySelector("summary")?.focus(); }
  });

  document.querySelectorAll("[data-asset-back]").forEach(button => {
    button.addEventListener("click", () => {
      const pane = document.getElementById(`p-${button.dataset.assetBack}`);
      pane?.classList.remove("asset-detail-open");
      pane?.querySelector(".report-item.on .report-item-open, .memory-item.on")?.focus({ preventScroll: true });
    });
  });
  const memoryDialog = document.getElementById("memoryCreateDialog");
  memoryDialog?.addEventListener("click", event => {
    if (event.target !== memoryDialog) return;
    const bounds = memoryDialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) memoryDialog.close();
  });

  // The shared reader can be opened from a report as well as the library.
  const libraryReader = document.getElementById("libraryReaderModal");
  if (libraryReader) document.body.appendChild(libraryReader);

  const shortcut = document.querySelector(".button-shortcut");
  if (shortcut && !/Mac|iPhone|iPad/.test(navigator.platform || "")) shortcut.textContent = "Ctrl↵";

  if ("scrollRestoration" in history) history.scrollRestoration = "manual";
  const initial = document.querySelector(".tab.on") || tabs[0];
  setMobileNav(false);
  syncActiveTab(initial);
  nav.scrollTop = 0;
  window.addEventListener("pageshow", () => requestAnimationFrame(() => {
    nav.scrollTop = 0;
    window.scrollTo(0, 0);
  }), { once: true });
  requestAnimationFrame(() => window.scrollTo(0, 0));
  body.classList.add("ui-ready");
  const revealWorkspace = () => requestAnimationFrame(() => requestAnimationFrame(dismissBootScreen));
  if (document.readyState === "complete") revealWorkspace();
  else window.addEventListener("load", revealWorkspace, { once: true });
})();
