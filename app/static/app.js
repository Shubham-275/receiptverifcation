/**
 * Receipt Intelligence — purchasing ledger controller.
 *
 * A dependency-free client over the existing API: analytics, receipt archive,
 * ingestion and the natural-language query engine. Display strings from the
 * API are escaped before they meet HTML.
 */
(() => {
  "use strict";

  const API = {
    health: "/api/v1/health/ready",
    authStatus: "/api/v1/auth/status",
    authLogin: "/api/v1/auth/login",
    authSetup: "/api/v1/auth/setup",
    overview: "/api/v1/analytics/overview",
    products: "/api/v1/analytics/products",
    monthly: "/api/v1/analytics/spending/monthly?months=9",
    composition: "/api/v1/analytics/spending/composition",
    receipts: "/api/v1/receipts",
    query: "/api/v1/queries",
    upload: "/api/v1/receipts/upload",
    extract: "/api/v1/receipts/extract",
    batchUpload: "/api/v1/receipts/batch-upload",
    batchExtract: "/api/v1/receipts/batch-extract",
  };

  const ROUTES = ["overview", "analytics", "inventory", "receipts", "intelligence"];
  const ROUTE_LABELS = {
    overview: "Overview",
    analytics: "Analytics",
    inventory: "Inventory",
    receipts: "Receipts",
    intelligence: "Intelligence",
  };
  const MAX_FILE_BYTES = 10 * 1024 * 1024;
  const ACCEPTED_EXTENSIONS = /\.(jpe?g|png|webp|heic|heif)$/i;
  const MONTH_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const MONTH_LONG = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];

  const state = {
    route: "overview",
    overview: null,
    products: [],
    monthly: [],
    composition: null,
    receipts: [],
    receiptsTotal: 0,
    lastLoadedAt: null,
    loading: false,
    offline: false,
    errors: { overview: null, products: null, monthly: null, composition: null, receipts: null },
    inventory: { query: "", category: "all", sort: "spend" },
    receiptFilter: { query: "", month: "all" },
    receiptRequest: 0,
    productRequest: 0,
    toastTimer: null,
    scan: {
      files: [],
      mode: "persist",
      processing: false,
      lastResult: null,
      lastMode: "persist",
      lastBatch: null,
      errorRetry: "choose",
    },
  };

  const $ = (id) => document.getElementById(id);

  const authState = {
    user: null,
    mode: "login",
    busy: false,
    booted: false,
  };

  // ── Utility ───────────────────────────────────────────────────────────────

  function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/`/g, "&#096;");
  }

  function numberOrNull(value) {
    if (value === null || value === undefined || value === "") return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function formatNumber(value, digits = 0) {
    const n = numberOrNull(value);
    if (n === null) return "—";
    return new Intl.NumberFormat("en-IN", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(n);
  }

  function formatMoney(value, digits = 2) {
    const n = numberOrNull(value);
    if (n === null) return "—";
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: "INR",
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(n);
  }

  function formatCompactMoney(value) {
    return formatMoney(value, 0);
  }

  function pluralUnit(unit, quantity) {
    const u = String(unit || "unit");
    if (Math.abs(Number(quantity)) === 1) return u;
    if (/^(kg|g|l|ml|litre|liter)$/i.test(u)) return u.toLowerCase() === "litre" || u.toLowerCase() === "liter" ? "litres" : u;
    if (/^box$/i.test(u)) return "boxes";
    if (/^(piece|packet|bottle|carton|dozen|bag|can)$/i.test(u)) return `${u}s`;
    return u;
  }

  function formatQuantity(quantity, unit) {
    const n = numberOrNull(quantity);
    if (n === null) return "—";
    const qty = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(n);
    return `${qty} ${pluralUnit(unit, n)}`;
  }

  function parseDate(value) {
    if (!value) return null;
    if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
    const text = String(value);
    const match = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (match) {
      const d = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
      return Number.isNaN(d.getTime()) ? null : d;
    }
    const d = new Date(text);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  function formatDate(value, options = {}) {
    const d = parseDate(value);
    if (!d) return "—";
    return new Intl.DateTimeFormat("en-GB", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      timeZone: "UTC",
      ...options,
    }).format(d);
  }

  function dateBadge(value) {
    const d = parseDate(value);
    if (!d) return { day: "—", month: "—" };
    return {
      day: new Intl.DateTimeFormat("en-GB", { day: "2-digit", timeZone: "UTC" }).format(d),
      month: new Intl.DateTimeFormat("en-GB", { month: "short", timeZone: "UTC" }).format(d),
    };
  }

  function formatTime(date) {
    if (!(date instanceof Date) || Number.isNaN(date.getTime())) return "—";
    return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" }).format(date);
  }

  function formatLocalDate(date) {
    if (!(date instanceof Date) || Number.isNaN(date.getTime())) return "—";
    return new Intl.DateTimeFormat("en-GB", {
      day: "2-digit", month: "short", year: "numeric",
    }).format(date);
  }

  function monthKey(value) {
    return value ? String(value).slice(0, 7) : "";
  }

  function monthLabelFromKey(key) {
    if (!key || !key.includes("-")) return "—";
    const [year, month] = key.split("-").map(Number);
    return `${MONTH_LONG[month - 1] || "Month"} ${year}`;
  }

  function icon(name, className = "icon") {
    return `<svg class="${className}" aria-hidden="true"><use href="#i-${name}"></use></svg>`;
  }

  function tableErrorRow(colspan, message) {
    return `<tr><td colspan="${colspan}"><p class="table-error">${escapeHtml(message)}</p></td></tr>`;
  }

  function emptyRow(colspan, message) {
    return `<tr><td colspan="${colspan}"><p class="empty-copy">${escapeHtml(message)}</p></td></tr>`;
  }

  function showToast(message) {
    const toast = $("toast");
    if (!toast) return;
    toast.textContent = message;
    toast.hidden = false;
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => { toast.hidden = true; }, 4200);
  }

  async function readResponse(response) {
    const text = await response.text();
    if (!text) return {};
    try { return JSON.parse(text); } catch { return { message: text }; }
  }

  async function getJSON(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      ...options,
      headers: { Accept: "application/json", ...(options.headers || {}) },
    });
    const data = await readResponse(response);
    if (!response.ok) {
      if (response.status === 401 && authState.user) {
        authState.user = null;
        showAuthScreen({
          mode: "login",
          message: "Your session expired. Sign in to continue.",
        });
      }
      const detail = data && (data.detail || data.message || data.error);
      const error = new Error(typeof detail === "string" ? detail : `Request failed (${response.status})`);
      error.status = response.status;
      error.data = data;
      throw error;
    }
    return data;
  }

  function errorText(error, fallback = "The request could not be completed.") {
    if (!error) return fallback;
    const data = error.data || {};
    if (typeof data.detail === "string" && data.detail.trim()) return data.detail;
    if (Array.isArray(data.detail)) {
      const parts = data.detail.map((item) => item.msg || JSON.stringify(item)).filter(Boolean);
      if (parts.length) return parts.join("; ");
    }
    if (typeof data.message === "string" && data.message.trim()) return data.message;
    if (error.message && !/^Request failed/.test(error.message)) return error.message;
    return fallback;
  }

  function isDatabaseError(error) {
    if (!error) return false;
    const data = error.data || {};
    if (data.error === "DATABASE_ERROR") return true;
    const message = `${data.message || ""} ${data.detail || ""} ${error.message || ""}`;
    return /postgres|database|pgvector/i.test(message);
  }

  function checkRowMath(item) {
    const qty = Number(item.quantity);
    const unitPrice = numberOrNull(item.unit_price);
    const lineTotal = numberOrNull(item.line_total);
    if (unitPrice === null || lineTotal === null || !Number.isFinite(qty)) {
      return { ok: true, detail: "" };
    }
    const expected = qty * unitPrice;
    const difference = Math.abs(expected - lineTotal);
    const tolerance = Math.max(lineTotal * 0.02, 2);
    if (difference > tolerance) {
      return {
        ok: false,
        detail: `${qty} × ${formatMoney(unitPrice)} = ${formatMoney(expected)}, but the line total reads ${formatMoney(lineTotal)}`,
      };
    }
    return { ok: true, detail: "" };
  }

  function sortedReceipts() {
    return [...state.receipts].sort((a, b) => {
      const dateA = String(a.receipt_date || "");
      const dateB = String(b.receipt_date || "");
      if (dateA !== dateB) return dateB.localeCompare(dateA);
      return String(b.created_at || "").localeCompare(String(a.created_at || ""));
    });
  }

  function receiptCategories() {
    const set = new Set();
    state.products.forEach((product) => {
      if (product.category) set.add(product.category);
    });
    return [...set].sort((a, b) => a.localeCompare(b));
  }

  function statusTag(status) {
    const value = String(status || "stored").toLowerCase();
    if (value === "stored" || value === "verified") {
      return `<span class="tag tag--accent">Verified</span>`;
    }
    if (value === "needs_review" || value === "review") {
      return `<span class="tag">Review</span>`;
    }
    if (value === "processing") {
      return `<span class="tag tag--plain">Processing</span>`;
    }
    return `<span class="tag tag--plain">${escapeHtml(value.replace(/_/g, " "))}</span>`;
  }

  // ── Routing and navigation ────────────────────────────────────────────────

  function currentRouteFromHash() {
    const hash = window.location.hash.replace(/^#\/?/, "").toLowerCase();
    return ROUTES.includes(hash) ? hash : "overview";
  }

  function renderRoute(route, { initial = false } = {}) {
    const changed = state.route !== route || initial;
    state.route = route;

    document.querySelectorAll("[data-view]").forEach((view) => {
      view.hidden = view.dataset.view !== route;
    });

    document.querySelectorAll("[data-route-link]").forEach((link) => {
      const active = link.dataset.routeLink === route;
      link.classList.toggle("is-active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });

    $("current-view-label").textContent = ROUTE_LABELS[route];
    document.title = `${ROUTE_LABELS[route]} — Receipt Intelligence`;

    if (changed) {
      const view = document.querySelector(`[data-view="${route}"]`);
      if (view) {
        view.classList.remove("is-entering");
        void view.offsetWidth;
        view.classList.add("is-entering");
      }
      if (!initial) {
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
    }
    closeMobileNav();
  }

  function goTo(route) {
    const target = ROUTES.includes(route) ? route : "overview";
    if (window.location.hash !== `#${target}`) {
      window.location.hash = `#${target}`;
    }
    renderRoute(target);
  }

  function openMobileNav() {
    $("sidebar").classList.add("is-open");
    $("nav-scrim").hidden = false;
    $("nav-toggle").setAttribute("aria-expanded", "true");
  }

  function closeMobileNav() {
    $("sidebar").classList.remove("is-open");
    $("nav-scrim").hidden = true;
    $("nav-toggle").setAttribute("aria-expanded", "false");
  }

  function toggleMobileNav() {
    if ($("sidebar").classList.contains("is-open")) closeMobileNav();
    else openMobileNav();
  }

  // ── Data loading and connection state ─────────────────────────────────────

  function updateSyncUI() {
    const dot = $("status-dot");
    const label = $("sync-label");
    const time = $("sync-time");
    const topbar = $("topbar-sync");
    const metaUpdated = $("meta-updated");

    dot.classList.toggle("is-offline", state.offline);

    if (state.loading) {
      label.textContent = "Syncing";
      time.textContent = "Reading ledger";
      topbar.textContent = "Syncing ledger";
      return;
    }

    if (state.offline) {
      label.textContent = "Offline";
      time.textContent = "PostgreSQL unavailable";
      topbar.textContent = "Read-only · database offline";
      if (!state.lastLoadedAt) metaUpdated.textContent = "Database offline";
      return;
    }

    if (state.lastLoadedAt) {
      const clock = formatTime(state.lastLoadedAt);
      label.textContent = "Connected";
      time.textContent = `${clock} · ${state.receiptsTotal || 0} invoices`;
      topbar.textContent = `Ledger synced · ${clock}`;
      metaUpdated.textContent = `${formatLocalDate(state.lastLoadedAt)} · ${clock}`;
    } else {
      label.textContent = "Connecting";
      time.textContent = "—";
      topbar.textContent = "Syncing ledger";
    }
  }

  function applyOfflineState(offline) {
    state.offline = offline;
    $("offline-banner").hidden = !offline;

    if (offline) {
      if (state.scan.mode === "persist") setScanMode("preview");
      const persistBtn = document.querySelector('[data-scan-mode="persist"]');
      if (persistBtn) {
        persistBtn.disabled = true;
        persistBtn.classList.add("is-disabled");
      }
    } else {
      const persistBtn = document.querySelector('[data-scan-mode="persist"]');
      if (persistBtn) {
        persistBtn.disabled = false;
        persistBtn.classList.remove("is-disabled");
      }
    }
    updateSyncUI();
  }

  function renderOfflinePlaceholders() {
    $("total-spend-value").textContent = "—";
    $("total-spend-foot").textContent = "Ledger database unavailable";
    $("kpi-month-label").textContent = "Last month";
    $("kpi-month-value").textContent = "—";
    $("kpi-month-foot").textContent = "No analytics available";
    $("kpi-receipts").textContent = "—";
    $("kpi-receipts-foot").textContent = "Database offline";
    $("kpi-products").textContent = "—";
    $("kpi-products-foot").textContent = "Database offline";
    $("kpi-suppliers").textContent = "—";
    $("kpi-suppliers-foot").textContent = "Database offline";
    $("overview-window").textContent = "Database offline";
    $("meta-window").textContent = "—";
    $("chart-total").textContent = "—";
    $("chart-foot").textContent = "Analytics unavailable";
    $("spend-chart").innerHTML = '<p class="empty-copy">PostgreSQL is offline. Start the database to load monthly purchasing data.</p>';
    $("analytics-total").textContent = "—";
    $("analytics-window").textContent = "Database offline";
    $("analytics-items").textContent = "—";
    $("analytics-items-foot").textContent = "Database offline";
    $("analytics-average-invoice").textContent = "—";
    $("analytics-average-invoice-foot").textContent = "Database offline";
    $("analytics-items-per-invoice").textContent = "—";
    $("analytics-items-per-invoice-foot").textContent = "Database offline";
    $("analytics-lead-category").textContent = "—";
    $("analytics-lead-category-foot").textContent = "Database offline";
    $("category-donut").innerHTML = '<p class="empty-copy">Category composition requires the ledger database.</p>';
    $("supplier-share").innerHTML = '<p class="empty-copy">Supplier concentration requires the ledger database.</p>';
    $("volume-chart").innerHTML = '<p class="empty-copy">Monthly volume requires the ledger database.</p>';
    $("top-products").innerHTML = '<p class="empty-copy">Product ranking requires the ledger database.</p>';
    $("supplier-concentration").textContent = "—";
    $("top-products-foot").textContent = "—";
    $("mix-chart").innerHTML = '<p class="empty-copy">Category balance requires the ledger database.</p>';
    $("mix-insights").innerHTML = "<li>Category insight requires the ledger database.</li>";
    $("recent-receipts").innerHTML = '<p class="empty-copy">The receipt archive is unavailable while PostgreSQL is offline.</p>';
    $("overview-inventory-body").innerHTML = tableErrorRow(6, "Inventory requires the ledger database.");
    $("inventory-body").innerHTML = tableErrorRow(8, "Inventory requires the ledger database.");
    $("receipts-body").innerHTML = tableErrorRow(7, "The archive requires the ledger database.");
    $("inventory-result-count").textContent = "—";
    $("receipts-result-count").textContent = "—";
    $("overview-product-count").textContent = "—";
  }

  async function loadAll({ silent = false } = {}) {
    if (state.loading) return;
    const refreshBtn = $("refresh-btn");
    state.loading = true;
    if (!silent) refreshBtn.classList.add("is-spinning");
    updateSyncUI();

    let health = null;
    let healthFailed = false;
    try {
      health = await getJSON(API.health);
    } catch {
      healthFailed = true;
    }

    const databaseReady = !healthFailed && health && String(health.database).toLowerCase() === "ok";
    if (!databaseReady) {
      applyOfflineState(true);
      state.loading = false;
      refreshBtn.classList.remove("is-spinning");
      updateSyncUI();
      if (!state.overview && state.receipts.length === 0) renderOfflinePlaceholders();
      if (!silent) {
        showToast(healthFailed
          ? "The ledger service is unreachable. Check the server and database."
          : "PostgreSQL is offline. Preview scanning is still available.");
      }
      return;
    }

    const results = await Promise.allSettled([
      getJSON(API.overview),
      getJSON(API.products),
      getJSON(API.monthly),
      getJSON(API.composition),
      getJSON(`${API.receipts}?limit=100`),
    ]);

    const [overviewResult, productsResult, monthlyResult, compositionResult, receiptsResult] = results;

    if (overviewResult.status === "fulfilled") {
      state.overview = overviewResult.value;
      state.errors.overview = null;
    } else {
      state.errors.overview = errorText(overviewResult.reason, "Overview unavailable.");
    }

    if (productsResult.status === "fulfilled") {
      state.products = Array.isArray(productsResult.value) ? productsResult.value : [];
      state.errors.products = null;
    } else {
      state.errors.products = errorText(productsResult.reason, "Inventory unavailable.");
    }

    if (monthlyResult.status === "fulfilled") {
      state.monthly = Array.isArray(monthlyResult.value) ? monthlyResult.value : [];
      state.errors.monthly = null;
    } else {
      state.errors.monthly = errorText(monthlyResult.reason, "Monthly analytics unavailable.");
    }

    if (compositionResult.status === "fulfilled") {
      state.composition = compositionResult.value || null;
      state.errors.composition = null;
    } else {
      state.composition = null;
      state.errors.composition = errorText(compositionResult.reason, "Spending composition unavailable.");
    }

    if (receiptsResult.status === "fulfilled") {
      const payload = receiptsResult.value || {};
      state.receipts = Array.isArray(payload.receipts) ? payload.receipts : [];
      state.receiptsTotal = Number(payload.total) || state.receipts.length;
      state.errors.receipts = null;
    } else {
      state.errors.receipts = errorText(receiptsResult.reason, "Receipt archive unavailable.");
    }

    const databaseFailure = results.some(
      (result) => result.status === "rejected" && isDatabaseError(result.reason)
    );
    applyOfflineState(databaseFailure);

    if (!databaseFailure) {
      state.lastLoadedAt = new Date();
    }

    state.loading = false;
    refreshBtn.classList.remove("is-spinning");
    renderAll();
    updateSyncUI();

    if (!silent && databaseFailure) {
      showToast("Some ledger data could not be loaded. PostgreSQL may be offline.");
    }
  }

  // ── Overview ──────────────────────────────────────────────────────────────

  function focusMonth() {
    if (!state.monthly.length) return null;
    const now = new Date();
    const latest = state.monthly[state.monthly.length - 1];
    if (latest.year === now.getFullYear() && latest.month === now.getMonth() + 1) {
      return state.monthly.length > 1 ? state.monthly[state.monthly.length - 2] : latest;
    }
    return latest;
  }

  function renderOverview() {
    const overview = state.overview;

    if (overview) {
      const spend = numberOrNull(overview.total_spend) ?? 0;
      $("total-spend-value").textContent = formatNumber(spend, 0);
      $("total-spend-value").title = formatMoney(spend);
      $("total-spend-foot").textContent = `Across ${overview.total_receipts} verified invoice${overview.total_receipts === 1 ? "" : "s"} · through ${formatDate(overview.latest_date, { day: "2-digit", month: "short" })}`;

      $("kpi-receipts").textContent = formatNumber(overview.total_receipts);
      $("kpi-receipts-foot").textContent = overview.earliest_date
        ? `Since ${formatDate(overview.earliest_date, { day: "2-digit", month: "short", year: "numeric" })}`
        : "No documents filed";

      $("kpi-products").textContent = formatNumber(overview.total_products);
      $("kpi-products-foot").textContent = `${receiptCategories().length} categories tracked`;

      $("kpi-suppliers").textContent = formatNumber(overview.total_suppliers);
      $("kpi-suppliers-foot").textContent = "Resolved from invoice headers";

      const month = focusMonth();
      const periodTotal = state.monthly.reduce((sum, item) => sum + (numberOrNull(item.total_spending) ?? 0), 0);
      if (month) {
        const share = periodTotal > 0 ? (month.total_spending / periodTotal) * 100 : 0;
        $("kpi-month-label").textContent = `${month.month_label} spend`;
        $("kpi-month-value").textContent = formatCompactMoney(month.total_spending);
        $("kpi-month-foot").textContent = `${month.receipt_count} invoice${month.receipt_count === 1 ? "" : "s"} · ${share.toFixed(1)}% of period spend`;
      } else {
        $("kpi-month-label").textContent = "Last month";
        $("kpi-month-value").textContent = "—";
        $("kpi-month-foot").textContent = "No monthly records";
      }

      $("meta-window").textContent = overview.earliest_date && overview.latest_date
        ? `${formatDate(overview.earliest_date, { day: "2-digit", month: "short" })} — ${formatDate(overview.latest_date)}`
        : "—";

      $("nav-count-inventory").textContent = String(overview.total_products);
      $("nav-count-receipts").textContent = String(overview.total_receipts);
    } else if (state.errors.overview) {
      $("total-spend-value").textContent = "—";
      $("total-spend-foot").textContent = state.errors.overview;
      $("kpi-month-value").textContent = "—";
      $("kpi-receipts").textContent = "—";
      $("kpi-products").textContent = "—";
      $("kpi-suppliers").textContent = "—";
    }

    if (state.monthly.length) {
      const first = state.monthly[0];
      const last = state.monthly[state.monthly.length - 1];
      const range = first.year === last.year
        ? `${MONTH_SHORT[first.month - 1]} — ${MONTH_SHORT[last.month - 1]} ${last.year}`
        : `${first.month_label} — ${last.month_label}`;
      $("overview-window").textContent = range;
    } else if (!state.errors.monthly) {
      $("overview-window").textContent = "No recorded period";
    }

    renderSpendChart();
    renderRecentReceipts();
    renderOverviewInventory();
  }

  function renderSpendChart() {
    const chart = $("spend-chart");
    const total = state.monthly.reduce((sum, item) => sum + (numberOrNull(item.total_spending) ?? 0), 0);
    const invoiceCount = state.monthly.reduce((sum, item) => sum + (Number(item.receipt_count) || 0), 0);

    $("chart-total").textContent = formatCompactMoney(total);

    if (!state.monthly.length) {
      chart.innerHTML = `<p class="empty-copy">${escapeHtml(state.errors.monthly || "No monthly purchasing records yet.")}</p>`;
      $("chart-foot").textContent = state.errors.monthly ? "Analytics unavailable" : "No invoices in this period";
      return;
    }

    const max = Math.max(1, ...state.monthly.map((item) => numberOrNull(item.total_spending) ?? 0));
    const grid = Array.from({ length: 5 }, () => "<span></span>").join("");
    const bars = state.monthly.map((item, index) => {
      const value = numberOrNull(item.total_spending) ?? 0;
      const height = value > 0 ? Math.max(2, (value / max) * 100) : 0;
      const current = index === state.monthly.length - 1;
      const label = `${item.month_label}: ${formatMoney(value)} across ${item.receipt_count} invoice${item.receipt_count === 1 ? "" : "s"}`;
      return `<div class="bar-col${current ? " is-current" : ""}">
        <button type="button" class="bar-col__bar" style="height:${height.toFixed(2)}%" aria-label="${escapeAttr(label)}" title="${escapeAttr(label)}">
          <span class="bar-col__tip">${escapeHtml(formatCompactMoney(value))}</span>
        </button>
      </div>`;
    }).join("");
    const labels = state.monthly.map((item, index) => {
      const current = index === state.monthly.length - 1;
      return `<span class="bar-chart__label${current ? " is-current" : ""}">${escapeHtml(MONTH_SHORT[item.month - 1] || item.month_label)}</span>`;
    }).join("");

    chart.innerHTML = `<div class="bar-chart__plot">
      <div class="bar-chart__grid">${grid}</div>
      <div class="bar-chart__bars">${bars}</div>
    </div>
    <div class="bar-chart__labels">${labels}</div>`;

    const first = state.monthly[0];
    const last = state.monthly[state.monthly.length - 1];
    const range = first.year === last.year
      ? `${MONTH_SHORT[first.month - 1]} — ${MONTH_SHORT[last.month - 1]} ${last.year}`
      : `${first.month_label} — ${last.month_label}`;
    $("chart-foot").textContent = `${range} · ${invoiceCount} invoices`;
  }

  function renderRecentReceipts() {
    const list = $("recent-receipts");
    const receipts = sortedReceipts().slice(0, 5);

    if (!receipts.length) {
      list.innerHTML = `<p class="empty-copy">${escapeHtml(state.errors.receipts || "No invoices filed yet. Scan a receipt to begin.")}</p>`;
      return;
    }

    list.innerHTML = receipts.map((receipt) => {
      const date = dateBadge(receipt.receipt_date);
      const supplier = receipt.supplier_name || "Unknown supplier";
      const invoice = receipt.invoice_number ? `Invoice ${receipt.invoice_number}` : "No invoice number";
      const meta = `${invoice} · ${receipt.item_count || 0} item${receipt.item_count === 1 ? "" : "s"}`;
      const label = `${supplier}, ${formatDate(receipt.receipt_date)}, ${formatMoney(receipt.receipt_total)}`;
      return `<button type="button" class="invoice-row" data-receipt-id="${escapeAttr(receipt.id)}" aria-label="${escapeAttr(label)}">
        <span class="invoice-row__date"><strong>${date.day}</strong><span>${date.month}</span></span>
        <span class="invoice-row__body">
          <span class="invoice-row__supplier">${escapeHtml(supplier)}</span>
          <span class="invoice-row__meta">${escapeHtml(meta)}</span>
        </span>
        <span class="invoice-row__amount">${escapeHtml(formatMoney(receipt.receipt_total))}</span>
        ${icon("arrow", "icon invoice-row__arrow")}
      </button>`;
    }).join("");
  }

  function renderOverviewInventory() {
    const body = $("overview-inventory-body");
    const products = [...state.products]
      .sort((a, b) => (numberOrNull(b.total_spend) ?? 0) - (numberOrNull(a.total_spend) ?? 0))
      .slice(0, 5);

    $("overview-product-count").textContent = state.products.length
      ? `${state.products.length} products tracked`
      : "—";

    if (!products.length) {
      body.innerHTML = state.errors.products
        ? tableErrorRow(6, state.errors.products)
        : emptyRow(6, "No products in the catalogue yet.");
      return;
    }

    body.innerHTML = products.map((product) => `<tr data-clickable="true" tabindex="0" role="button" data-product-id="${escapeAttr(product.id)}" aria-label="Open ledger for ${escapeAttr(product.canonical_name)}">
      <td class="cell-product">
        <span class="product-name">${escapeHtml(product.canonical_name)}</span>
        <span class="product-meta">${product.purchase_count} order${product.purchase_count === 1 ? "" : "s"}</span>
      </td>
      <td><span class="category-label">${escapeHtml(product.category || "Uncategorised")}</span></td>
      <td class="num">${escapeHtml(formatQuantity(product.total_quantity, product.unit))}</td>
      <td class="num">${escapeHtml(formatMoney(product.avg_price))}</td>
      <td class="num cell-amount">${escapeHtml(formatCompactMoney(product.total_spend))}</td>
      <td class="num">${escapeHtml(formatDate(product.last_purchased_date, { day: "2-digit", month: "short" }))}</td>
    </tr>`).join("");
  }

  // ── Analytics ─────────────────────────────────────────────────────────────

  const CHART_COLORS = [
    "#a64a2c", "#171613", "#5d5951", "#837e73",
    "#aaa496", "#c9c2b4", "#e2ddd1",
  ];

  function formatCompactIndian(value) {
    const n = numberOrNull(value) ?? 0;
    if (n >= 10000000) return `₹${(n / 10000000).toFixed(2)}Cr`;
    if (n >= 100000) return `₹${(n / 100000).toFixed(2)}L`;
    if (n >= 1000) return `₹${(n / 1000).toFixed(0)}k`;
    return `₹${formatNumber(n, 0)}`;
  }

  function median(values) {
    const nums = values.map(Number).filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
    if (!nums.length) return null;
    const middle = Math.floor(nums.length / 2);
    return nums.length % 2 ? nums[middle] : (nums[middle - 1] + nums[middle]) / 2;
  }

  function niceCeil(value) {
    if (!Number.isFinite(value) || value <= 0) return 1;
    const magnitude = 10 ** Math.floor(Math.log10(value));
    const normalized = value / magnitude;
    const step = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((candidate) => normalized <= candidate) || 10;
    return step * magnitude;
  }

  function donutMarkup(categories) {
    const items = categories.filter((item) => (numberOrNull(item.total_spend) ?? 0) > 0);
    const total = items.reduce((sum, item) => sum + (numberOrNull(item.total_spend) ?? 0), 0);
    if (!items.length || total <= 0) {
      return '<p class="empty-copy">No categorised line items recorded yet.</p>';
    }

    const cx = 118;
    const cy = 118;
    const radius = 88;
    const circumference = 2 * Math.PI * radius;
    let offset = 0;

    const segments = items.map((item, index) => {
      const value = numberOrNull(item.total_spend) ?? 0;
      const length = (value / total) * circumference;
      const gap = items.length > 1 ? 1.5 : 0;
      const dash = Math.max(0, length - gap);
      const color = CHART_COLORS[index % CHART_COLORS.length];
      const share = ((value / total) * 100).toFixed(1);
      const title = `${item.category}: ${formatMoney(value)} · ${share}% · ${item.line_count} line items`;
      const segment = `<circle class="donut__segment" cx="${cx}" cy="${cy}" r="${radius}" fill="none" stroke="${color}" stroke-width="32" stroke-dasharray="${dash.toFixed(2)} ${(circumference - dash).toFixed(2)}" stroke-dashoffset="${(-offset).toFixed(2)}" transform="rotate(-90 ${cx} ${cy})"><title>${escapeHtml(title)}</title></circle>`;
      offset += length;
      return segment;
    }).join("");

    const legend = items.map((item, index) => {
      const value = numberOrNull(item.total_spend) ?? 0;
      const share = total ? (value / total) * 100 : 0;
      return `<li class="donut-legend__row">
        <span class="donut-legend__swatch" style="background:${CHART_COLORS[index % CHART_COLORS.length]}" aria-hidden="true"></span>
        <span class="donut-legend__copy">
          <span class="donut-legend__name">${escapeHtml(item.category || "Uncategorised")}</span>
          <span class="donut-legend__meta">${formatNumber(item.line_count)} lines · ${formatNumber(item.product_count)} product${item.product_count === 1 ? "" : "s"}</span>
        </span>
        <span class="donut-legend__share">${share.toFixed(1)}%</span>
        <span class="donut-legend__value">${escapeHtml(formatCompactMoney(value))}</span>
      </li>`;
    }).join("");

    return `<div class="donut">
      <svg viewBox="0 0 236 236" role="img" aria-label="Spending share by category">
        <circle class="donut__track" cx="${cx}" cy="${cy}" r="${radius}" fill="none" stroke-width="32"/>
        ${segments}
        <text class="donut__total" x="${cx}" y="110" text-anchor="middle">${escapeHtml(formatCompactIndian(total))}</text>
        <text class="donut__caption" x="${cx}" y="130" text-anchor="middle">Total spend</text>
      </svg>
    </div>
    <ul class="donut-legend">${legend}</ul>`;
  }

  function rankRows(items) {
    const max = Math.max(1, ...items.map((item) => item.value));
    return items.map((item, index) => {
      const width = Math.max(1.5, (item.value / max) * 100);
      return `<div class="rank-row">
        <div class="rank-row__head">
          <span class="rank-row__name" title="${escapeAttr(item.name)}">${escapeHtml(item.name)}</span>
          <span class="rank-row__value">${escapeHtml(item.valueLabel)}</span>
        </div>
        <div class="rank-row__track"><span class="rank-row__fill${index === 0 ? " is-lead" : ""}" style="width:${width.toFixed(2)}%"></span></div>
        <div class="rank-row__meta"><span>${escapeHtml(item.meta)}</span><span>${item.share.toFixed(1)}%</span></div>
      </div>`;
    }).join("");
  }

  function comboChartMarkup(months) {
    const data = months.filter((item) => item && Number.isFinite(Number(item.total_spending)));
    if (!data.length) {
      return '<p class="empty-copy">No monthly flow recorded yet.</p>';
    }

    const width = 820;
    const height = 300;
    const left = 58;
    const right = 54;
    const top = 18;
    const bottom = 42;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;
    const maxSpend = niceCeil(Math.max(...data.map((item) => numberOrNull(item.total_spending) ?? 0)));
    const peakItems = Math.max(...data.map((item) => Number(item.item_count) || 0));
    const maxItems = Math.max(4, Math.ceil((peakItems * 1.18) / 4) * 4);
    const band = plotWidth / data.length;
    const barWidth = Math.min(48, band * 0.46);
    const xCenter = (index) => left + band * index + band / 2;

    const grid = Array.from({ length: 5 }, (_, index) => {
      const ratio = index / 4;
      const y = top + plotHeight * ratio;
      const spendValue = maxSpend * (1 - ratio);
      const itemValue = maxItems * (1 - ratio);
      return `<line class="combo-grid" x1="${left}" y1="${y.toFixed(1)}" x2="${width - right}" y2="${y.toFixed(1)}"/>
        <text class="combo-axis-label" x="${left - 10}" y="${(y + 3).toFixed(1)}" text-anchor="end">${escapeHtml(formatCompactIndian(spendValue))}</text>
        <text class="combo-axis-label" x="${width - right + 10}" y="${(y + 3).toFixed(1)}" text-anchor="start">${escapeHtml(formatNumber(itemValue, 0))}</text>`;
    }).join("");

    const bars = data.map((item, index) => {
      const value = numberOrNull(item.total_spending) ?? 0;
      const barHeight = Math.max(1, (value / maxSpend) * plotHeight);
      const x = xCenter(index) - barWidth / 2;
      const y = top + plotHeight - barHeight;
      const current = index === data.length - 1;
      const title = `${item.month_label}: ${formatMoney(value)} across ${item.receipt_count} invoice${item.receipt_count === 1 ? "" : "s"} · ${item.item_count} line items`;
      return `<rect class="combo-bar${current ? " is-current" : ""}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barWidth.toFixed(1)}" height="${barHeight.toFixed(1)}"><title>${escapeHtml(title)}</title></rect>`;
    }).join("");

    const pointY = (value) => top + plotHeight - ((Number(value) || 0) / maxItems) * plotHeight;
    const linePath = data.map((item, index) => `${index === 0 ? "M" : "L"}${xCenter(index).toFixed(1)},${pointY(item.item_count).toFixed(1)}`).join(" ");
    const dots = data.map((item, index) => {
      const current = index === data.length - 1;
      return `<circle class="combo-dot${current ? " is-current" : ""}" cx="${xCenter(index).toFixed(1)}" cy="${pointY(item.item_count).toFixed(1)}" r="${current ? 3.6 : 2.8}"><title>${escapeHtml(`${item.month_label}: ${item.item_count} line items`)}</title></circle>`;
    }).join("");

    const labels = data.map((item, index) => {
      return `<text class="combo-month" x="${xCenter(index).toFixed(1)}" y="${height - 16}" text-anchor="middle">${escapeHtml(MONTH_SHORT[item.month - 1] || item.month_label)}</text>`;
    }).join("");

    return `<svg class="combo-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Monthly spend and line-item volume">
      ${grid}
      <line class="combo-axis" x1="${left}" y1="${top}" x2="${left}" y2="${top + plotHeight}"/>
      <line class="combo-axis" x1="${width - right}" y1="${top}" x2="${width - right}" y2="${top + plotHeight}"/>
      ${bars}
      <path class="combo-line" d="${linePath}"/>
      ${dots}
      ${labels}
    </svg>`;
  }

  function mixChartMarkup(categories, totalSpend, totalItems) {
    if (!categories.length) {
      return '<p class="empty-copy">No category composition recorded yet.</p>';
    }

    return `<div class="mix-list">${categories.map((category) => {
      const spendShare = totalSpend ? ((numberOrNull(category.total_spend) ?? 0) / totalSpend) * 100 : 0;
      const itemShare = totalItems ? ((Number(category.line_count) || 0) / totalItems) * 100 : 0;
      return `<div class="mix-row">
        <div class="mix-row__head">
          <span class="mix-row__name">${escapeHtml(category.category || "Uncategorised")}</span>
          <span class="mix-row__values">
            <span class="mix-value mix-value--spend">${spendShare.toFixed(1)}%</span>
            <span class="mix-value mix-value--items">${itemShare.toFixed(1)}%</span>
          </span>
        </div>
        <div class="mix-row__bars" aria-hidden="true">
          <span class="mix-bar mix-bar--spend" style="width:${spendShare.toFixed(2)}%" title="Spend share ${spendShare.toFixed(1)}%"></span>
          <span class="mix-bar mix-bar--items" style="width:${itemShare.toFixed(2)}%" title="Item share ${itemShare.toFixed(1)}%"></span>
        </div>
      </div>`;
    }).join("")}</div>`;
  }

  function renderMixInsights(categories, totalSpend, totalItems) {
    const list = $("mix-insights");
    if (!categories.length || !totalSpend || !totalItems) {
      list.innerHTML = "<li>No category insight available yet.</li>";
      return;
    }

    const scored = categories.map((category) => {
      const spendShare = ((numberOrNull(category.total_spend) ?? 0) / totalSpend) * 100;
      const itemShare = ((Number(category.line_count) || 0) / totalItems) * 100;
      return { category, spendShare, itemShare, gap: spendShare - itemShare };
    }).sort((a, b) => b.gap - a.gap);

    const spendHeavy = scored[0];
    const volumeHeavy = scored[scored.length - 1];
    const topTwoSpend = scored
      .slice()
      .sort((a, b) => b.spendShare - a.spendShare)
      .slice(0, 2)
      .reduce((sum, item) => sum + item.spendShare, 0);

    const insights = [
      `<li><span class="insight-list__num">01</span><p><strong>${escapeHtml(spendHeavy.category.category || "Uncategorised")}</strong> carries ${spendHeavy.spendShare.toFixed(1)}% of spend from ${spendHeavy.itemShare.toFixed(1)}% of line items — value is concentrated in fewer entries.</p></li>`,
      `<li><span class="insight-list__num">02</span><p><strong>${escapeHtml(volumeHeavy.category.category || "Uncategorised")}</strong> accounts for ${volumeHeavy.itemShare.toFixed(1)}% of line items but ${volumeHeavy.spendShare.toFixed(1)}% of spend — volume is doing more of the work here.</p></li>`,
      `<li><span class="insight-list__num">03</span><p>The two largest categories hold ${topTwoSpend.toFixed(1)}% of spend; the remaining ${Math.max(0, categories.length - 2)} categories share the rest.</p></li>`,
    ];

    list.innerHTML = insights.join("");
  }

  function renderAnalytics() {
    const composition = state.composition;
    const overview = state.overview;

    if (!composition) {
      const message = state.errors.composition || "Spending composition is unavailable.";
      $("analytics-total").textContent = "—";
      $("analytics-window").textContent = "—";
      $("analytics-items").textContent = "—";
      $("analytics-items-foot").textContent = message;
      $("analytics-average-invoice").textContent = "—";
      $("analytics-average-invoice-foot").textContent = "—";
      $("analytics-items-per-invoice").textContent = "—";
      $("analytics-items-per-invoice-foot").textContent = "—";
      $("analytics-lead-category").textContent = "—";
      $("analytics-lead-category-foot").textContent = "—";
      $("category-count").textContent = "—";
      $("category-donut").innerHTML = `<p class="empty-copy">${escapeHtml(message)}</p>`;
      $("supplier-share").innerHTML = `<p class="empty-copy">${escapeHtml(message)}</p>`;
      $("supplier-concentration").textContent = "—";
      $("volume-chart").innerHTML = `<p class="empty-copy">${escapeHtml(message)}</p>`;
      $("volume-foot").textContent = "—";
      $("top-products").innerHTML = `<p class="empty-copy">${escapeHtml(message)}</p>`;
      $("top-products-foot").textContent = "—";
      $("mix-chart").innerHTML = `<p class="empty-copy">${escapeHtml(message)}</p>`;
      $("mix-insights").innerHTML = `<li>${escapeHtml(message)}</li>`;
      return;
    }

    const totalSpend = numberOrNull(composition.total_spend) ?? 0;
    const totalReceipts = Number(composition.total_receipts) || 0;
    const totalItems = Number(composition.total_line_items) || 0;
    const categories = Array.isArray(composition.categories) ? composition.categories : [];
    const suppliers = Array.isArray(composition.suppliers) ? composition.suppliers : [];
    const monthlyVolume = Array.isArray(composition.monthly_volume) ? composition.monthly_volume : [];

    $("analytics-total").textContent = formatCompactMoney(totalSpend);
    $("analytics-total").title = formatMoney(totalSpend);
    $("analytics-window").textContent = overview?.earliest_date && overview?.latest_date
      ? `${formatDate(overview.earliest_date, { day: "2-digit", month: "short" })} — ${formatDate(overview.latest_date)}`
      : "All recorded invoices";

    $("analytics-items").textContent = formatNumber(totalItems);
    $("analytics-items-foot").textContent = `Across ${formatNumber(totalReceipts)} verified invoice${totalReceipts === 1 ? "" : "s"}`;

    const invoiceValues = state.receipts.map((receipt) => numberOrNull(receipt.receipt_total)).filter((value) => value !== null);
    const averageInvoice = numberOrNull(composition.average_invoice) ?? (totalReceipts ? totalSpend / totalReceipts : 0);
    $("analytics-average-invoice").textContent = formatCompactMoney(averageInvoice);
    $("analytics-average-invoice-foot").textContent = invoiceValues.length
      ? `Range ${formatCompactMoney(Math.min(...invoiceValues))} — ${formatCompactMoney(Math.max(...invoiceValues))}`
      : "No invoice values recorded";

    const itemsPerInvoice = numberOrNull(composition.average_items_per_invoice) ?? (totalReceipts ? totalItems / totalReceipts : 0);
    const medianItems = median(state.receipts.map((receipt) => receipt.item_count));
    $("analytics-items-per-invoice").textContent = formatNumber(itemsPerInvoice, 1);
    $("analytics-items-per-invoice-foot").textContent = medianItems === null
      ? "No line items recorded"
      : `Median ${formatNumber(medianItems, 1)} across all invoices`;

    const leadCategory = categories[0] || null;
    if (leadCategory) {
      const leadShare = totalSpend ? (leadCategory.total_spend / totalSpend) * 100 : 0;
      $("analytics-lead-category").textContent = leadCategory.category || "Uncategorised";
      $("analytics-lead-category-foot").textContent = `${formatCompactMoney(leadCategory.total_spend)} · ${leadShare.toFixed(1)}% of spend`;
    } else {
      $("analytics-lead-category").textContent = "—";
      $("analytics-lead-category-foot").textContent = "No categories recorded";
    }

    $("category-count").textContent = categories.length
      ? `${categories.length} tracked`
      : "—";
    $("category-donut").innerHTML = donutMarkup(categories);

    const supplierItems = suppliers.slice(0, 6).map((supplier) => {
      const value = numberOrNull(supplier.total_spend) ?? 0;
      return {
        name: supplier.supplier_name || "Unknown supplier",
        value,
        valueLabel: formatCompactMoney(value),
        meta: `${formatNumber(supplier.invoice_count)} invoice${supplier.invoice_count === 1 ? "" : "s"}`,
        share: totalSpend ? (value / totalSpend) * 100 : 0,
      };
    });
    $("supplier-share").innerHTML = supplierItems.length
      ? rankRows(supplierItems)
      : '<p class="empty-copy">No supplier invoices recorded yet.</p>';
    const topTwoShare = suppliers.slice(0, 2).reduce((sum, supplier) => sum + (numberOrNull(supplier.total_spend) ?? 0), 0);
    $("supplier-concentration").textContent = suppliers.length
      ? `${suppliers.length} suppliers · top two hold ${(totalSpend ? (topTwoShare / totalSpend) * 100 : 0).toFixed(1)}%`
      : "No supplier records";

    $("volume-chart").innerHTML = comboChartMarkup(monthlyVolume);
    const monthsWithItems = monthlyVolume.filter((month) => Number(month.item_count) > 0).length;
    const busiest = [...monthlyVolume].sort((a, b) => Number(b.item_count) - Number(a.item_count))[0];
    $("volume-foot").textContent = busiest && Number(busiest.item_count) > 0
      ? `${monthsWithItems} active months · busiest ${busiest.month_label} with ${busiest.item_count} lines`
      : "No monthly item volume recorded";

    const productTotal = state.products.reduce((sum, product) => sum + (numberOrNull(product.total_spend) ?? 0), 0);
    const topProducts = [...state.products]
      .sort((a, b) => (numberOrNull(b.total_spend) ?? 0) - (numberOrNull(a.total_spend) ?? 0))
      .slice(0, 6)
      .map((product) => {
        const value = numberOrNull(product.total_spend) ?? 0;
        return {
          name: product.canonical_name || "Unnamed product",
          value,
          valueLabel: formatCompactMoney(value),
          meta: `${product.category || "Uncategorised"} · ${formatNumber(product.purchase_count)} lines`,
          share: productTotal ? (value / productTotal) * 100 : 0,
        };
      });
    $("top-products").innerHTML = topProducts.length
      ? rankRows(topProducts)
      : '<p class="empty-copy">No product spend recorded yet.</p>';
    const topShare = topProducts.reduce((sum, product) => sum + product.value, 0);
    $("top-products-foot").textContent = topProducts.length
      ? `Top ${topProducts.length} of ${state.products.length} products · ${(productTotal ? (topShare / productTotal) * 100 : 0).toFixed(1)}% of spend`
      : "—";

    $("mix-chart").innerHTML = mixChartMarkup(categories, totalSpend, totalItems);
    renderMixInsights(categories, totalSpend, totalItems);
  }

  // ── Inventory ─────────────────────────────────────────────────────────────

  function filteredProducts() {
    const query = state.inventory.query.trim().toLowerCase();
    let products = state.products.filter((product) => {
      const categoryMatch = state.inventory.category === "all" || product.category === state.inventory.category;
      const searchMatch = !query
        || String(product.canonical_name || "").toLowerCase().includes(query)
        || String(product.category || "").toLowerCase().includes(query);
      return categoryMatch && searchMatch;
    });

    const sort = state.inventory.sort;
    products.sort((a, b) => {
      if (sort === "name") return String(a.canonical_name).localeCompare(String(b.canonical_name));
      if (sort === "volume") return (numberOrNull(b.total_quantity) ?? 0) - (numberOrNull(a.total_quantity) ?? 0);
      if (sort === "recent") return String(b.last_purchased_date || "").localeCompare(String(a.last_purchased_date || ""));
      return (numberOrNull(b.total_spend) ?? 0) - (numberOrNull(a.total_spend) ?? 0);
    });
    return products;
  }

  function renderInventory() {
    const categories = receiptCategories();
    const filterContainer = $("inventory-categories");
    if (state.inventory.category !== "all" && !categories.includes(state.inventory.category)) {
      state.inventory.category = "all";
    }

    filterContainer.innerHTML = [
      `<button type="button" class="filter-btn${state.inventory.category === "all" ? " is-active" : ""}" data-category="all">All</button>`,
      ...categories.map((category) => `<button type="button" class="filter-btn${state.inventory.category === category ? " is-active" : ""}" data-category="${escapeAttr(category)}">${escapeHtml(category)}</button>`),
    ].join("");

    const products = filteredProducts();
    const body = $("inventory-body");
    const totalSpend = state.products.reduce((sum, product) => sum + (numberOrNull(product.total_spend) ?? 0), 0);

    $("inventory-total").textContent = state.products.length ? formatCompactMoney(totalSpend) : "—";
    $("inventory-count").textContent = state.products.length
      ? `${state.products.length} products · ${categories.length} categories`
      : "No catalogue records";
    $("inventory-result-count").textContent = state.products.length
      ? `Showing ${products.length} of ${state.products.length} products`
      : "—";
    $("nav-count-inventory").textContent = state.products.length ? String(state.products.length) : "—";

    if (!state.products.length) {
      body.innerHTML = state.errors.products
        ? tableErrorRow(8, state.errors.products)
        : emptyRow(8, "No products recorded yet. Scan a supplier invoice to build the catalogue.");
      return;
    }

    if (!products.length) {
      body.innerHTML = emptyRow(8, `No products match “${state.inventory.query.trim()}”.`);
      return;
    }

    body.innerHTML = products.map((product) => `<tr data-clickable="true" tabindex="0" role="button" data-product-id="${escapeAttr(product.id)}" aria-label="Open ledger for ${escapeAttr(product.canonical_name)}">
      <td class="cell-product">
        <span class="product-name">${escapeHtml(product.canonical_name)}</span>
        <span class="product-meta">${product.purchase_count} order${product.purchase_count === 1 ? "" : "s"} · per ${escapeHtml(product.unit || "unit")}</span>
      </td>
      <td><span class="category-label">${escapeHtml(product.category || "Uncategorised")}</span></td>
      <td class="num">${escapeHtml(formatQuantity(product.total_quantity, product.unit))}</td>
      <td class="num">${escapeHtml(formatMoney(product.avg_price))}</td>
      <td class="num cell-amount">${escapeHtml(formatCompactMoney(product.total_spend))}</td>
      <td class="num">${escapeHtml(String(product.purchase_count || 0))}</td>
      <td class="num">${escapeHtml(formatDate(product.last_purchased_date))}</td>
      <td class="cell-arrow">${icon("arrow", "icon row-arrow")}</td>
    </tr>`).join("");
  }

  // ── Receipt archive ───────────────────────────────────────────────────────

  function ensureMonthOptions() {
    const select = $("receipts-month");
    const keys = new Set();
    state.receipts.forEach((receipt) => {
      if (receipt.receipt_date) keys.add(monthKey(receipt.receipt_date));
    });
    const sorted = [...keys].sort((a, b) => b.localeCompare(a));
    const current = state.receiptFilter.month;
    if (current !== "all" && !keys.has(current)) state.receiptFilter.month = "all";

    select.innerHTML = [
      '<option value="all">All months</option>',
      ...sorted.map((key) => `<option value="${escapeAttr(key)}">${escapeHtml(monthLabelFromKey(key))}</option>`),
    ].join("");
    select.value = state.receiptFilter.month;
  }

  function filteredReceipts() {
    const query = state.receiptFilter.query.trim().toLowerCase();
    const month = state.receiptFilter.month;
    return sortedReceipts().filter((receipt) => {
      const monthMatch = month === "all" || monthKey(receipt.receipt_date) === month;
      if (!monthMatch) return false;
      if (!query) return true;
      return [
        receipt.supplier_name,
        receipt.invoice_number,
        receipt.receipt_date,
        receipt.id,
        receipt.status,
      ].some((value) => String(value || "").toLowerCase().includes(query));
    });
  }

  function renderReceipts() {
    ensureMonthOptions();
    const receipts = filteredReceipts();
    const body = $("receipts-body");
    const totalSpend = state.receipts.reduce((sum, receipt) => sum + (numberOrNull(receipt.receipt_total) ?? 0), 0);
    const months = new Set(state.receipts.map((receipt) => monthKey(receipt.receipt_date)).filter(Boolean));

    $("receipts-total").textContent = state.receiptsTotal ? formatNumber(state.receiptsTotal) : "—";
    $("receipts-count").textContent = state.receipts.length
      ? `${formatCompactMoney(totalSpend)} across ${state.receipts.length} documents · ${months.size} months`
      : "No documents filed";
    $("receipts-result-count").textContent = state.receipts.length
      ? `Showing ${receipts.length} of ${state.receipts.length} documents`
      : "—";
    $("nav-count-receipts").textContent = state.receiptsTotal ? String(state.receiptsTotal) : "—";

    if (!state.receipts.length) {
      body.innerHTML = state.errors.receipts
        ? tableErrorRow(7, state.errors.receipts)
        : emptyRow(7, "No receipts filed yet. Use “Scan receipt” to add the first document.");
      return;
    }

    if (!receipts.length) {
      body.innerHTML = emptyRow(7, "No documents match the current search and month filter.");
      return;
    }

    body.innerHTML = receipts.map((receipt) => {
      const supplier = receipt.supplier_name || "Unknown supplier";
      const meta = `${(receipt.id || "").slice(0, 8)}${receipt.id ? "…" : ""} · ${receipt.currency || "INR"}`;
      return `<tr data-clickable="true" tabindex="0" role="button" data-receipt-id="${escapeAttr(receipt.id)}" aria-label="Open receipt from ${escapeAttr(supplier)} dated ${escapeAttr(formatDate(receipt.receipt_date))}">
        <td>${escapeHtml(formatDate(receipt.receipt_date))}</td>
        <td class="cell-product">
          <span class="product-name">${escapeHtml(supplier)}</span>
          <span class="product-meta">${escapeHtml(meta)}</span>
        </td>
        <td><span class="row-invoice">${escapeHtml(receipt.invoice_number || "—")}</span></td>
        <td class="num">${escapeHtml(String(receipt.item_count || 0))}</td>
        <td class="num cell-amount">${escapeHtml(formatMoney(receipt.receipt_total))}</td>
        <td>${statusTag(receipt.status)}</td>
        <td class="cell-arrow">${icon("arrow", "icon row-arrow")}</td>
      </tr>`;
    }).join("");
  }

  function csvCell(value) {
    if (value === null || value === undefined) return '""';
    return `"${String(value).replace(/"/g, '""')}"`;
  }

  function exportReceipts() {
    const receipts = filteredReceipts();
    if (!receipts.length) {
      showToast("There are no receipts in the current view to export.");
      return;
    }
    const header = ["Date", "Supplier", "Invoice", "Items", "Total INR", "Status", "Receipt ID"];
    const rows = receipts.map((receipt) => [
      receipt.receipt_date || "",
      receipt.supplier_name || "",
      receipt.invoice_number || "",
      receipt.item_count || 0,
      numberOrNull(receipt.receipt_total) ?? "",
      receipt.status || "",
      receipt.id || "",
    ]);
    const csv = "\ufeff" + [header, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `receipts-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    showToast(`Exported ${receipts.length} document${receipts.length === 1 ? "" : "s"} to CSV.`);
  }

  // ── Receipt drawer ────────────────────────────────────────────────────────

  async function openReceipt(receiptId) {
    if (!receiptId) return;
    $("receipt-dialog-label").textContent = "Receipt detail";
    $("receipt-content").innerHTML = '<p class="drawer-loading">Retrieving the stored document…</p>';
    openDialog("receipt-dialog");

    const request = ++state.receiptRequest;
    try {
      const receipt = await getJSON(`${API.receipts}/${encodeURIComponent(receiptId)}`);
      if (request !== state.receiptRequest) return;
      renderReceiptDetail(receipt);
    } catch (error) {
      if (request !== state.receiptRequest) return;
      $("receipt-content").innerHTML = `<p class="drawer-error">${escapeHtml(errorText(error, "The receipt could not be retrieved."))}</p>`;
    }
  }

  function openReceiptData(receipt, { preview = false } = {}) {
    $("receipt-dialog-label").textContent = preview ? "Preview receipt" : "Receipt detail";
    renderReceiptDetail(receipt, { preview });
    openDialog("receipt-dialog");
  }

  function renderReceiptDetail(receipt, { preview = false } = {}) {
    const items = Array.isArray(receipt.items) ? receipt.items : [];
    const checks = items.map(checkRowMath);
    const badRows = checks
      .map((check, index) => (check.ok ? null : index + 1))
      .filter((value) => value !== null);
    const itemsSum = items.reduce((sum, item) => sum + (numberOrNull(item.line_total) ?? 0), 0);
    const statedTotal = numberOrNull(receipt.receipt_total);
    const totalDifference = statedTotal === null ? null : Math.abs(itemsSum - statedTotal);
    const totalTolerance = statedTotal === null ? 0 : Math.max(statedTotal * 0.05, 5);
    const totalMatches = statedTotal === null || itemsSum === 0 ? null : totalDifference <= totalTolerance;

    const supplier = receipt.supplier_name || receipt.supplier || "Unknown supplier";
    const receiptDate = receipt.receipt_date || receipt.date;
    const isPreview = preview || !receipt.id;
    const confidence = numberOrNull(receipt.extraction_confidence);
    const classifier = numberOrNull(receipt.classifier_confidence);
    const shortId = receipt.id ? String(receipt.id).slice(0, 8) : "new";

    let lineArithmeticLabel = "No lines to check";
    let lineArithmeticClass = "value--ok";
    if (items.length) {
      if (badRows.length) {
        lineArithmeticLabel = `${badRows.length} of ${items.length} line${badRows.length === 1 ? "" : "s"} to review`;
        lineArithmeticClass = "value--warn";
      } else {
        lineArithmeticLabel = `All ${items.length} line${items.length === 1 ? "" : "s"} reconcile`;
      }
    }

    let totalLabel = "Not stated on invoice";
    let totalClass = "value--ok";
    if (statedTotal !== null) {
      if (totalMatches === null) {
        totalLabel = "No item subtotal";
      } else if (totalMatches) {
        totalLabel = "Matches item subtotal";
      } else {
        totalLabel = `${formatMoney(totalDifference)} variance`;
        totalClass = "value--warn";
      }
    }

    const notes = [];
    if (badRows.length) {
      notes.push(`${badRows.length} line item${badRows.length === 1 ? "" : "s"} need review: quantity × unit price does not match the printed line total.`);
    }
    if (totalMatches === false) {
      notes.push(`The item subtotal is ${formatMoney(itemsSum)} while the invoice is stated at ${formatMoney(statedTotal)}. Tax, rounding or a discount may explain the difference.`);
    }
    if (receipt.validation_notes) notes.push(String(receipt.validation_notes));
    if (isPreview) notes.push("Preview extraction only — nothing has been written to PostgreSQL.");

    const linesHtml = items.length ? `<table class="receipt-lines">
      <thead><tr><th scope="col">Product</th><th scope="col" class="num">Qty</th><th scope="col" class="num">Unit price</th><th scope="col" class="num">Line total</th></tr></thead>
      <tbody>${items.map((item, index) => {
        const check = checks[index];
        const name = item.product_name || item.raw_name || "Unnamed item";
        const raw = item.raw_name && item.raw_name !== name
          ? `<span class="line-raw">${escapeHtml(item.raw_name)}</span>`
          : "";
        const flag = check.ok
          ? ""
          : `<span class="line-flag" title="${escapeAttr(check.detail)}">Check</span>`;
        return `<tr>
          <td><span class="line-name">${escapeHtml(name)}</span>${raw}${flag}</td>
          <td class="num">${escapeHtml(formatQuantity(item.quantity, item.unit))}</td>
          <td class="num">${escapeHtml(formatMoney(item.unit_price))}</td>
          <td class="num${check.ok ? "" : " line-warn"}">${escapeHtml(formatMoney(item.line_total))}</td>
        </tr>`;
      }).join("")}</tbody>
    </table>` : '<p class="empty-copy">No line items were extracted from this document.</p>';

    const totalRows = [];
    if (items.length) {
      totalRows.push(`<div class="receipt-total-row"><span>Item subtotal</span><strong>${escapeHtml(formatMoney(itemsSum))}</strong></div>`);
    }
    if (statedTotal !== null) {
      totalRows.push(`<div class="receipt-total-row"><span>Stated invoice total</span><strong>${escapeHtml(formatMoney(statedTotal))}</strong></div>`);
      if (totalDifference !== null && totalDifference > 0.005) {
        totalRows.push(`<div class="receipt-total-row"><span>Variance</span><strong class="${totalMatches ? "" : "value--warn"}">${escapeHtml(formatMoney(totalDifference))}</strong></div>`);
      }
    }
    totalRows.push(`<div class="receipt-total-row receipt-total-row--grand"><span>${isPreview ? "Verified total" : "Invoice total"}</span><strong>${escapeHtml(formatMoney(statedTotal))}</strong></div>`);

    const sourceTitle = receipt.image_path
      ? String(receipt.image_path)
      : isPreview ? "Preview extraction" : "Source image not retained";
    const sourceMeta = [
      classifier !== null ? `Classifier ${Math.round(classifier * 100)}%` : null,
      confidence !== null ? `Extraction ${Math.round(confidence * 100)}%` : null,
      receipt.created_at && !isPreview ? `Filed ${formatDate(receipt.created_at)}` : null,
    ].filter(Boolean).join(" · ");

    $("receipt-content").innerHTML = `
      <header class="receipt-head">
        <div>
          <p class="micro">${isPreview ? "Preview extraction" : "Verified receipt"} · ${escapeHtml(shortId.toUpperCase())}</p>
          <h2 class="receipt-head__supplier">${escapeHtml(supplier)}</h2>
          <div class="receipt-head__meta">
            <span>${escapeHtml(formatDate(receiptDate))}</span>
            <span class="dot" aria-hidden="true"></span>
            <span>${receipt.invoice_number ? `Invoice ${escapeHtml(receipt.invoice_number)}` : "No invoice number"}</span>
            <span class="dot" aria-hidden="true"></span>
            <span>${items.length} item${items.length === 1 ? "" : "s"}</span>
          </div>
        </div>
        <div class="receipt-head__total">
          <span class="micro">Invoice total</span>
          <strong>${escapeHtml(formatMoney(statedTotal))}</strong>
        </div>
      </header>

      <dl class="verification-strip">
        <div><dt>Line arithmetic</dt><dd class="${lineArithmeticClass}">${escapeHtml(lineArithmeticLabel)}</dd></div>
        <div><dt>Invoice total</dt><dd class="${totalClass}">${escapeHtml(totalLabel)}</dd></div>
        <div><dt>Vision confidence</dt><dd class="${confidence !== null && confidence < 0.8 ? "value--warn" : "value--ok"}">${confidence !== null ? `${(confidence * 100).toFixed(1)}%` : "Not recorded"}</dd></div>
      </dl>

      <section class="receipt-section">
        <div class="section-head">
          <h3>Line items</h3>
          <span class="micro">${items.length} line${items.length === 1 ? "" : "s"}</span>
        </div>
        ${linesHtml}
      </section>

      <div class="receipt-total-block">${totalRows.join("")}</div>

      ${notes.length ? `<div class="receipt-note">${notes.map((note) => escapeHtml(note)).join("<br>")}</div>` : ""}

      <div class="receipt-source">
        <div class="receipt-source__placeholder" aria-hidden="true">${icon("file")}</div>
        <div class="receipt-source__copy">
          <p>${escapeHtml(sourceTitle)}</p>
          <p>${escapeHtml(sourceMeta || (isPreview ? "Not persisted to the ledger." : "Stored document"))}</p>
        </div>
      </div>`;
  }

  // ── Product drawer ────────────────────────────────────────────────────────

  async function openProduct(productId) {
    if (!productId) return;
    const product = state.products.find((item) => String(item.id) === String(productId));
    $("product-dialog-label").textContent = "Product ledger";
    $("product-content").innerHTML = '<p class="drawer-loading">Reading the price ledger…</p>';
    openDialog("product-dialog");

    if (!product) {
      $("product-content").innerHTML = '<p class="drawer-error">This product is not present in the current catalogue.</p>';
      return;
    }

    const request = ++state.productRequest;
    const base = `/api/v1/analytics/products/${encodeURIComponent(product.id)}`;
    const results = await Promise.allSettled([
      getJSON(`${base}/monthly-stats?months=9`),
      getJSON(`${base}/suppliers`),
      getJSON(`${base}/history?limit=50`),
    ]);
    if (request !== state.productRequest) return;

    const [statsResult, suppliersResult, historyResult] = results;
    renderProductDetail(product, {
      stats: statsResult.status === "fulfilled" && Array.isArray(statsResult.value) ? statsResult.value : [],
      suppliers: suppliersResult.status === "fulfilled" && Array.isArray(suppliersResult.value) ? suppliersResult.value : [],
      history: historyResult.status === "fulfilled" && Array.isArray(historyResult.value) ? historyResult.value : [],
      statsError: statsResult.status === "rejected" ? errorText(statsResult.reason, "Price history unavailable.") : null,
      suppliersError: suppliersResult.status === "rejected" ? errorText(suppliersResult.reason, "Supplier comparison unavailable.") : null,
      historyError: historyResult.status === "rejected" ? errorText(historyResult.reason, "Purchase history unavailable.") : null,
    });
  }

  function priceChartMarkup(stats, unit) {
    const points = stats.filter((item) => numberOrNull(item.avg_price) !== null);
    if (!points.length) {
      return '<div class="chart-empty">No monthly price history recorded.</div>';
    }

    const width = 640;
    const height = 220;
    const left = 48;
    const right = 14;
    const top = 14;
    const bottom = 44;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;
    const values = points.map((item) => Number(item.avg_price));
    let min = Math.min(...values);
    let max = Math.max(...values);
    if (min === max) {
      min -= Math.max(1, min * 0.05);
      max += Math.max(1, max * 0.05);
    }
    const padding = (max - min) * 0.14;
    min = Math.max(0, min - padding);
    max += padding;

    const x = (index) => left + (points.length === 1 ? plotWidth / 2 : (index / (points.length - 1)) * plotWidth);
    const y = (value) => top + plotHeight - ((value - min) / (max - min)) * plotHeight;

    const grid = Array.from({ length: 4 }, (_, index) => {
      const value = max - ((max - min) / 3) * index;
      const yPos = y(value);
      return `<line class="chart-grid" x1="${left}" y1="${yPos.toFixed(1)}" x2="${width - right}" y2="${yPos.toFixed(1)}"/>
        <text x="0" y="${(yPos + 3).toFixed(1)}">₹${escapeHtml(formatNumber(value, 0))}</text>`;
    }).join("");

    const linePath = points.map((item, index) => `${index === 0 ? "M" : "L"}${x(index).toFixed(1)},${y(Number(item.avg_price)).toFixed(1)}`).join(" ");
    const baseline = top + plotHeight;
    const areaPath = `${linePath} L${x(points.length - 1).toFixed(1)},${baseline} L${x(0).toFixed(1)},${baseline} Z`;

    const dots = points.map((item, index) => {
      const last = index === points.length - 1;
      return `<circle class="chart-dot${last ? " chart-last" : ""}" cx="${x(index).toFixed(1)}" cy="${y(Number(item.avg_price)).toFixed(1)}" r="${last ? 3.4 : 2.6}">
        <title>${escapeHtml(`${item.month_label}: ${formatMoney(item.avg_price)} per ${item.unit || unit}`)}</title>
      </circle>`;
    }).join("");

    const labels = points.map((item, index) => {
      return `<text x="${x(index).toFixed(1)}" y="${height - 18}" text-anchor="middle">${escapeHtml(MONTH_SHORT[item.month - 1] || item.month_label)}</text>`;
    }).join("");

    return `<svg class="price-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Average buying price by month">
      ${grid}
      <path class="chart-area" d="${areaPath}"/>
      <path class="chart-line" d="${linePath}"/>
      ${dots}
      ${labels}
    </svg>`;
  }

  function renderProductDetail(product, { stats, suppliers, history, statsError, suppliersError, historyError }) {
    const unit = product.unit || stats[0]?.unit || history[0]?.unit || "unit";
    const prices = [
      ...stats.map((item) => numberOrNull(item.min_price)).filter((value) => value !== null),
      ...stats.map((item) => numberOrNull(item.max_price)).filter((value) => value !== null),
      ...history.map((item) => numberOrNull(item.unit_price)).filter((value) => value !== null),
    ];
    const lowest = prices.length ? Math.min(...prices) : null;
    const highest = prices.length ? Math.max(...prices) : null;

    const supplierRows = suppliers.length ? suppliers.map((supplier, index) => `<tr>
      <td><span class="supplier-name">${escapeHtml(supplier.supplier_name)}</span>${index === 0 ? '<span class="best-price">Best</span>' : ""}</td>
      <td class="num">${escapeHtml(formatMoney(supplier.avg_price))}</td>
      <td class="num">${escapeHtml(formatMoney(supplier.min_price))} – ${escapeHtml(formatMoney(supplier.max_price))}</td>
      <td class="num">${escapeHtml(formatQuantity(supplier.total_quantity, supplier.unit || unit))}</td>
      <td class="num">${escapeHtml(String(supplier.purchase_count || 0))}</td>
    </tr>`).join("") : "";

    const historyRows = history.length ? history.map((entry) => `<tr>
      <td>${escapeHtml(formatDate(entry.receipt_date))}</td>
      <td><span class="supplier-name">${escapeHtml(entry.supplier_name)}</span></td>
      <td class="num">${escapeHtml(formatQuantity(entry.quantity, entry.unit || unit))}</td>
      <td class="num">${escapeHtml(formatMoney(entry.unit_price))}</td>
      <td class="num">${escapeHtml(formatMoney(entry.line_total))}</td>
      <td class="num"><button type="button" class="text-button" data-open-receipt="${escapeAttr(entry.receipt_id)}">Open</button></td>
    </tr>`).join("") : "";

    $("product-content").innerHTML = `
      <header class="product-head">
        <div>
          <p class="micro">${escapeHtml(product.category || "Uncategorised")} · Product ledger</p>
          <h2>${escapeHtml(product.canonical_name)}</h2>
          <p class="micro">${product.purchase_count} order${product.purchase_count === 1 ? "" : "s"} · last bought ${escapeHtml(formatDate(product.last_purchased_date))}</p>
        </div>
        <div class="product-head__stat">
          <span class="micro">Average cost / ${escapeHtml(unit)}</span>
          <strong>${escapeHtml(formatMoney(product.avg_price))}</strong>
        </div>
      </header>

      <dl class="product-metrics">
        <div><span class="label">Total volume</span><span class="value">${escapeHtml(formatQuantity(product.total_quantity, unit))}</span></div>
        <div><span class="label">Recorded spend</span><span class="value">${escapeHtml(formatCompactMoney(product.total_spend))}</span></div>
        <div><span class="label">Orders</span><span class="value">${escapeHtml(String(product.purchase_count || 0))}</span></div>
        <div><span class="label">Price range</span><span class="value">${lowest === null ? "—" : `${escapeHtml(formatMoney(lowest))} – ${escapeHtml(formatMoney(highest))}`}</span></div>
      </dl>

      <section class="receipt-section">
        <div class="section-head">
          <h3>Buying price trend</h3>
          <span class="micro">${stats.length ? "Last 9 months" : "No data"} · per ${escapeHtml(unit)}</span>
        </div>
        ${statsError ? `<div class="chart-empty">${escapeHtml(statsError)}</div>` : priceChartMarkup(stats, unit)}
      </section>

      <section class="receipt-section">
        <div class="section-head">
          <h3>Supplier comparison</h3>
          <span class="micro">Ranked by average price</span>
        </div>
        ${suppliersError ? `<p class="drawer-error">${escapeHtml(suppliersError)}</p>` : suppliers.length ? `<table class="product-table">
          <thead><tr><th scope="col">Supplier</th><th scope="col" class="num">Avg</th><th scope="col" class="num">Range</th><th scope="col" class="num">Volume</th><th scope="col" class="num">Orders</th></tr></thead>
          <tbody>${supplierRows}</tbody>
        </table>` : '<p class="empty-copy">No supplier records for this product.</p>'}
      </section>

      <section class="receipt-section">
        <div class="section-head">
          <h3>Purchase history</h3>
          <span class="micro">${history.length} entr${history.length === 1 ? "y" : "ies"}</span>
        </div>
        ${historyError ? `<p class="drawer-error">${escapeHtml(historyError)}</p>` : history.length ? `<table class="product-table">
          <thead><tr><th scope="col">Date</th><th scope="col">Supplier</th><th scope="col" class="num">Qty</th><th scope="col" class="num">Unit price</th><th scope="col" class="num">Line total</th><th scope="col"><span class="sr-only">Open receipt</span></th></tr></thead>
          <tbody>${historyRows}</tbody>
        </table>` : '<p class="empty-copy">No purchase entries recorded.</p>'}
      </section>`;
  }

  // ── Natural-language query ────────────────────────────────────────────────

  function routeMeta(route) {
    if (route === "rag") {
      return {
        label: "Pgvector",
        note: "Semantic receipt retrieval",
      };
    }
    if (route === "sql_and_rag" || route === "hybrid") {
      return {
        label: "Hybrid",
        note: "Semantic recall + exact totals",
      };
    }
    return {
      label: "SQL",
      note: "Exact aggregation in PostgreSQL",
    };
  }

  function formatDuration(milliseconds) {
    const value = Number(milliseconds);
    if (!Number.isFinite(value)) return "—";
    if (value < 10) return `${value.toFixed(1)} ms`;
    return `${Math.round(value)} ms`;
  }

  function renderAnswerReceipts(receipts) {
    const container = $("ask-receipts");
    if (!Array.isArray(receipts) || !receipts.length) {
      container.innerHTML = "";
      return;
    }
    container.innerHTML = receipts.map((receipt) => {
      const supplier = receipt.supplier_name || "Unknown supplier";
      const meta = `${receipt.invoice_number ? `Invoice ${receipt.invoice_number}` : "No invoice"} · ${receipt.item_count || 0} items`;
      return `<button type="button" class="answer-receipt" data-receipt-id="${escapeAttr(receipt.id)}">
        <span class="answer-receipt__date">${escapeHtml(formatDate(receipt.receipt_date, { day: "2-digit", month: "short" }))}</span>
        <span>
          <span class="answer-receipt__supplier">${escapeHtml(supplier)}</span>
          <span class="answer-receipt__meta">${escapeHtml(meta)}</span>
        </span>
        <span class="answer-receipt__amount">${escapeHtml(formatMoney(receipt.receipt_total))}</span>
        ${icon("arrow")}
      </button>`;
    }).join("");
  }

  async function runQuery(question) {
    const query = String(question || "").trim();
    if (!query) return;

    const result = $("ask-result");
    const routeTag = $("ask-route");
    const askButton = $("ask-submit");
    const answerText = $("ask-answer");

    $("ask-input").value = query;
    result.hidden = false;
    result.classList.remove("is-error");
    result.classList.add("is-loading");
    routeTag.dataset.route = "working";
    routeTag.textContent = "Querying";
    $("ask-time").textContent = "—";
    $("ask-question-echo").textContent = query;
    answerText.textContent = "Reading the purchasing ledger…";
    $("ask-route-note").textContent = "Routing the question";
    $("ask-receipts").innerHTML = "";
    askButton.disabled = true;
    const label = askButton.querySelector("span");
    if (label) label.textContent = "Asking";

    try {
      const data = await getJSON(API.query, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });

      const meta = routeMeta(data.route);
      routeTag.dataset.route = data.route || "sql";
      routeTag.textContent = meta.label;
      result.classList.remove("is-loading", "is-error");
      answerText.textContent = data.answer || "The query engine returned no answer.";
      $("ask-time").textContent = formatDuration(data.execution_time_ms);
      $("ask-question-echo").textContent = data.query || query;
      $("ask-route-note").textContent = meta.note;
      renderAnswerReceipts(data.receipts);
    } catch (error) {
      result.classList.remove("is-loading");
      result.classList.add("is-error");
      routeTag.dataset.route = "error";
      routeTag.textContent = "Error";
      $("ask-time").textContent = "—";
      $("ask-route-note").textContent = "Query failed";
      answerText.textContent = errorText(error, "The query engine could not be reached.");
      $("ask-receipts").innerHTML = "";
      if (isDatabaseError(error)) applyOfflineState(true);
    } finally {
      askButton.disabled = false;
      if (label) label.textContent = "Ask";
    }
  }

  function askQuestion(question) {
    const query = String(question || "").trim();
    if (!query) return;
    goTo("intelligence");
    $("ask-input").value = query;
    runQuery(query);
    const result = $("ask-result");
    setTimeout(() => {
      result.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }, 80);
  }

  // ── Dialog helpers ────────────────────────────────────────────────────────

  function openDialog(id) {
    const dialog = $(id);
    if (dialog && !dialog.open) dialog.showModal();
  }

  function closeDialog(id) {
    const dialog = $(id);
    if (!dialog || !dialog.open) return true;
    if (id === "scan-dialog" && state.scan.processing) {
      showToast("Please wait for the current document to finish processing.");
      return false;
    }
    dialog.close();
    return true;
  }

  function closeScanDialog(force = false) {
    if (state.scan.processing && !force) {
      showToast("Please wait for the current document to finish processing.");
      return false;
    }
    const dialog = $("scan-dialog");
    if (dialog && dialog.open) dialog.close();
    return true;
  }

  // ── Scan: staging ─────────────────────────────────────────────────────────

  function setScanMode(mode) {
    let next = mode === "preview" ? "preview" : "persist";
    if (next === "persist" && state.offline) next = "preview";
    state.scan.mode = next;

    document.querySelectorAll("[data-scan-mode]").forEach((button) => {
      const active = button.dataset.scanMode === next;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });

    $("mode-note").textContent = next === "persist"
      ? "Persists the verified receipt and its line items to PostgreSQL."
      : "Extracts and verifies the document without writing to the database.";
    updateScanControls();
  }

  function updateScanControls() {
    const processing = state.scan.processing;
    document.querySelectorAll("[data-scan-mode]").forEach((button) => {
      const blocked = state.offline && button.dataset.scanMode === "persist";
      button.disabled = processing || blocked;
      button.classList.toggle("is-disabled", blocked);
    });
    $("scan-process").disabled = processing || state.scan.files.length === 0;
    $("scan-clear").disabled = processing || state.scan.files.length === 0;
    $("file-input").disabled = processing;
    $("scan-dialog").querySelector(".dialog__close").disabled = processing;
    $("scan-error-retry").disabled = processing;
    const addFiles = $("scan-add-files");
    if (addFiles) addFiles.disabled = processing;
    document.querySelectorAll(".dropzone__samples button").forEach((button) => {
      button.disabled = processing;
    });
    updateScanProcessLabel();
  }

  function updateScanProcessLabel() {
    const label = $("scan-process-label");
    if (!label) return;
    const count = state.scan.files.length;
    label.textContent = count > 1 ? `Process ${count} documents` : "Process document";
  }

  function revokeScanUrls() {
    state.scan.files.forEach((entry) => {
      if (entry.url) URL.revokeObjectURL(entry.url);
    });
  }

  function resetScanStage() {
    revokeScanUrls();
    state.scan.files = [];
    state.scan.lastResult = null;
    state.scan.lastBatch = null;
    $("file-input").value = "";
    $("scan-progress").hidden = true;
    $("scan-result").hidden = true;
    $("scan-batch-result").hidden = true;
    $("scan-error").hidden = true;
    $("scan-staged").hidden = true;
    $("dropzone").hidden = false;
    $("staged-list").innerHTML = "";
    updateScanControls();
  }

  function openScanDialog() {
    resetScanStage();
    setScanMode(state.offline ? "preview" : state.scan.mode);
    const dialog = $("scan-dialog");
    if (!dialog.open) dialog.showModal();
    setTimeout(() => $("dropzone").focus({ preventScroll: true }), 60);
  }

  function renderStaged() {
    const files = state.scan.files;
    $("staged-count").textContent = `${files.length} file${files.length === 1 ? "" : "s"}`;
    $("scan-staged").hidden = files.length === 0;
    $("dropzone").hidden = files.length > 0;

    $("staged-list").innerHTML = files.map((entry, index) => {
      const thumb = entry.url
        ? `<img class="staged-item__thumb" src="${escapeAttr(entry.url)}" alt="">`
        : `<div class="staged-item__thumb staged-item__thumb--empty" aria-hidden="true">${icon("file")}</div>`;
      return `<li class="staged-item">
        ${thumb}
        <div class="staged-item__info">
          <p class="staged-item__name" title="${escapeAttr(entry.file.name)}">${escapeHtml(entry.file.name)}</p>
          <p class="staged-item__size">${escapeHtml(formatBytes(entry.file.size))}</p>
        </div>
        <button type="button" class="staged-item__remove" data-remove-file="${index}" aria-label="Remove ${escapeAttr(entry.file.name)}">×</button>
      </li>`;
    }).join("");

    updateScanControls();
  }

  function formatBytes(bytes) {
    const value = Number(bytes) || 0;
    if (value < 1024) return `${value} B`;
    if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / 1048576).toFixed(1)} MB`;
  }

  function acceptedFile(file) {
    if (!file) return false;
    if (ACCEPTED_EXTENSIONS.test(file.name || "")) return true;
    return ["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"].includes(file.type);
  }

  function addFiles(fileList) {
    if (state.scan.processing) return;
    const incoming = Array.from(fileList || []);
    if (!incoming.length) return;

    const rejected = [];
    let added = 0;

    incoming.forEach((file) => {
      if (!acceptedFile(file)) {
        rejected.push(`${file.name} is not a supported image`);
        return;
      }
      if (file.size > MAX_FILE_BYTES) {
        rejected.push(`${file.name} exceeds the 10 MB limit`);
        return;
      }
      const duplicate = state.scan.files.some((entry) => (
        entry.file.name === file.name
        && entry.file.size === file.size
        && entry.file.lastModified === file.lastModified
      ));
      if (duplicate) return;

      const previewable = !/\.(heic|heif)$/i.test(file.name || "") && file.type !== "image/heic" && file.type !== "image/heif";
      state.scan.files.push({
        file,
        url: previewable ? URL.createObjectURL(file) : null,
      });
      added += 1;
    });

    if (added) {
      $("scan-result").hidden = true;
      $("scan-batch-result").hidden = true;
      $("scan-error").hidden = true;
      renderStaged();
    }

    if (rejected.length) {
      const more = rejected.length > 1 ? ` (+${rejected.length - 1} more)` : "";
      showToast(`${rejected[0]}${more}.`);
    }
  }

  function removeScanFile(index) {
    if (state.scan.processing) return;
    const [entry] = state.scan.files.splice(index, 1);
    if (entry && entry.url) URL.revokeObjectURL(entry.url);
    $("scan-result").hidden = true;
    $("scan-batch-result").hidden = true;
    $("scan-error").hidden = true;
    renderStaged();
  }

  // ── Scan: processing ─────────────────────────────────────────────────────

  function startScanProgress() {
    const steps = [...document.querySelectorAll(".pipeline li")];
    const started = Date.now();
    const messages = [
      "Validating the image…",
      "Classifying the document…",
      "Extracting every line item…",
      "Reconciling arithmetic…",
      "Resolving product identities…",
      "Finalising the ledger entry…",
    ];
    let current = 0;

    steps.forEach((step) => step.classList.remove("is-active", "is-done"));
    if (steps[0]) steps[0].classList.add("is-active");
    $("scan-progress-status").textContent = messages[0];
    $("scan-timer").textContent = "0.0s";

    const timer = setInterval(() => {
      const elapsed = ((Date.now() - started) / 1000).toFixed(1);
      $("scan-timer").textContent = `${elapsed}s`;
      if (current < steps.length - 1) {
        steps[current].classList.remove("is-active");
        steps[current].classList.add("is-done");
        current += 1;
        steps[current].classList.add("is-active");
        $("scan-progress-status").textContent = messages[current] || messages[messages.length - 1];
      }
    }, 1100);

    return {
      stop(complete = true) {
        clearInterval(timer);
        const elapsed = ((Date.now() - started) / 1000).toFixed(1);
        $("scan-timer").textContent = `${elapsed}s`;
        if (complete) {
          steps.forEach((step) => {
            step.classList.remove("is-active");
            step.classList.add("is-done");
          });
          $("scan-progress-status").textContent = "Ledger reconciled.";
        }
      },
    };
  }

  function showScanError(title, copy, retryMode = "choose") {
    state.scan.errorRetry = retryMode;
    $("scan-error-title").textContent = title;
    $("scan-error-copy").textContent = copy;
    $("scan-error-retry").textContent = retryMode === "staged" ? "Try again" : "Choose another";
    $("scan-error").hidden = false;
    $("scan-result").hidden = true;
    $("scan-batch-result").hidden = true;
    $("scan-progress").hidden = true;
    $("scan-staged").hidden = true;
    $("dropzone").hidden = true;
  }

  async function processScan() {
    if (state.scan.processing || !state.scan.files.length) return;
    const mode = state.scan.mode;
    const files = state.scan.files.map((entry) => entry.file);
    const batch = files.length > 1;
    const endpoint = batch
      ? (mode === "persist" ? API.batchUpload : API.batchExtract)
      : (mode === "persist" ? API.upload : API.extract);

    state.scan.processing = true;
    updateScanControls();
    $("scan-staged").hidden = true;
    $("dropzone").hidden = true;
    $("scan-result").hidden = true;
    $("scan-batch-result").hidden = true;
    $("scan-error").hidden = true;
    $("scan-progress").hidden = false;
    $("scan-progress").scrollIntoView({ behavior: "smooth", block: "nearest" });
    const progress = startScanProgress();

    const formData = new FormData();
    if (batch) files.forEach((file) => formData.append("files", file));
    else formData.append("file", files[0]);

    try {
      const response = await fetch(endpoint, {
        method: "POST",
        body: formData,
        headers: { Accept: "application/json" },
      });
      const data = await readResponse(response);
      progress.stop(response.ok);

      if (response.ok) {
        if (batch) renderBatchResult(data, mode);
        else renderScanResult(data, mode);
        if (mode === "persist") loadAll({ silent: true });
        return;
      }

      const message = data.detail || data.message || data.error || `The document could not be processed (${response.status}).`;
      if (response.status === 422) {
        showScanError(
          "Not accepted as a supplier receipt",
          `${message} Choose a clear photograph of a printed, handwritten or photographed supplier invoice.`,
          "choose"
        );
      } else if (response.status === 400) {
        showScanError("Image could not be read", `${message} Use a JPEG, PNG, WebP or HEIC file under 10 MB.`, "choose");
      } else if (response.status === 503) {
        const failure = Object.assign(new Error(message), { status: response.status, data });
        if (isDatabaseError(failure)) {
          applyOfflineState(true);
          setScanMode("preview");
          showScanError(
            "Database unavailable",
            `${message} Preview mode is now selected — process the document again to extract and verify it without saving.`,
            "staged"
          );
        } else {
          console.error("scan_ai_service_failed", data);
          const unavailable = /not found|no longer available|unavailable|high demand/i.test(message);
          showScanError(
            "Extraction service unavailable",
            unavailable
              ? "The configured vision model is unavailable. Check the AI provider configuration, then run the document again."
              : "The vision provider could not process this document. Check the AI provider configuration, then try again.",
            "staged"
          );
        }
      } else {
        showScanError("Document could not be processed", message, "staged");
      }
    } catch (error) {
      progress.stop(false);
      showScanError(
        "Connection error",
        "The verification server could not be reached. Check that the application is running, then try again.",
        "staged"
      );
    } finally {
      state.scan.processing = false;
      updateScanControls();
      $("scan-progress").hidden = true;
    }
  }

  function renderScanResult(data, mode) {
    const items = Array.isArray(data.items) ? data.items : [];
    const supplier = data.supplier_name || data.supplier || "Unidentified supplier";
    const date = data.receipt_date || data.date;
    const invoice = data.invoice_number;
    const total = numberOrNull(data.receipt_total);
    const confidence = numberOrNull(data.extraction_confidence);
    const statedTotal = total;
    const itemsSum = items.reduce((sum, item) => sum + (numberOrNull(item.line_total) ?? 0), 0);
    let mathFailures = Number(data.item_math_failures);
    if (!Number.isFinite(mathFailures)) {
      mathFailures = items.filter((item) => !checkRowMath(item).ok).length;
    }
    let totalsMatch = typeof data.total_match === "boolean" ? data.total_match : null;
    if (totalsMatch === null && statedTotal !== null && itemsSum > 0) {
      const tolerance = Math.max(statedTotal * 0.05, 5);
      totalsMatch = Math.abs(itemsSum - statedTotal) <= tolerance;
    }

    state.scan.lastResult = data;
    state.scan.lastMode = mode;

    $("scan-result-supplier").textContent = supplier;
    $("scan-result-meta").textContent = [
      date ? formatDate(date) : "Date not detected",
      invoice ? `Invoice ${invoice}` : "No invoice number",
      `${items.length} item${items.length === 1 ? "" : "s"}`,
      mode === "persist" ? "Saved to ledger" : "Preview only",
    ].join(" · ");
    $("scan-result-total").textContent = formatMoney(total);

    const arithmetic = mathFailures > 0
      ? `${mathFailures} line${mathFailures === 1 ? "" : "s"} to review`
      : totalsMatch === false ? "Total differs from items" : "Totals reconcile";

    $("scan-result-facts").innerHTML = `
      <div><dt>Handling</dt><dd>${mode === "persist" ? "Saved to ledger" : "Preview only"}</dd></div>
      <div><dt>Line items</dt><dd>${items.length} extracted</dd></div>
      <div><dt>Arithmetic</dt><dd class="${mathFailures > 0 || totalsMatch === false ? "fact-warn" : ""}">${escapeHtml(arithmetic)}</dd></div>
      <div><dt>Vision confidence</dt><dd>${confidence !== null ? `${(confidence * 100).toFixed(1)}%` : "Not recorded"}</dd></div>`;

    $("scan-result").hidden = false;
    $("scan-batch-result").hidden = true;
    $("scan-error").hidden = true;
    $("scan-progress").hidden = true;
    $("scan-staged").hidden = true;
    $("dropzone").hidden = true;
  }

  // ── Scan: batch result ────────────────────────────────────────────────────

  function renderBatchResult(data, mode) {
    const results = Array.isArray(data.results) ? data.results : [];
    const failed = Number(data.failed_count) || 0;
    const successful = Number(data.successful_count) || 0;

    state.scan.lastBatch = data;
    state.scan.lastMode = mode;

    $("batch-title").textContent = mode === "persist" ? "Documents filed" : "Documents verified";
    $("batch-meta").textContent = [
      `${successful} of ${data.total_files || results.length} accepted`,
      failed ? `${failed} rejected` : "No rejections",
      `${data.total_items || 0} items extracted`,
      mode === "persist" ? "Saved to ledger" : "Preview only",
    ].join(" · ");
    $("batch-total").textContent = formatMoney(data.total_spend);

    $("scan-batch-body").innerHTML = results.map((item, index) => {
      const entry = item.receipt || item.extraction;
      const success = Boolean(item.success && entry);
      const supplier = success ? (entry.supplier_name || entry.supplier || "Unknown supplier") : "—";
      const items = success ? (entry.item_count ?? (entry.items || []).length) : "—";
      const total = success ? numberOrNull(entry.receipt_total) : null;
      const label = success
        ? `<span class="tag tag--accent">${mode === "persist" ? "Verified" : "Preview"}</span>`
        : `<span class="tag tag--plain" title="${escapeAttr(item.detail || item.error || "Rejected")}">Rejected</span>`;
      const action = success
        ? `<button type="button" class="text-button" data-batch-inspect="${index}">Inspect</button>`
        : "";
      return `<tr>
        <td><span class="product-name">${escapeHtml(item.filename || "document")}</span><span class="product-meta">${success && entry.invoice_number ? `Invoice ${escapeHtml(entry.invoice_number)}` : escapeHtml(item.error || "Not accepted")}</span></td>
        <td>${escapeHtml(supplier)}</td>
        <td class="num">${escapeHtml(String(items))}</td>
        <td class="num">${escapeHtml(total === null ? "—" : formatMoney(total))}</td>
        <td>${label}</td>
        <td class="num">${action}</td>
      </tr>`;
    }).join("");

    $("scan-batch-result").hidden = false;
    $("scan-result").hidden = true;
    $("scan-error").hidden = true;
    $("scan-progress").hidden = true;
    $("scan-staged").hidden = true;
    $("dropzone").hidden = true;
  }

  // ── Sample documents ──────────────────────────────────────────────────────

  function canvasToFile(canvas, filename) {
    return new Promise((resolve, reject) => {
      canvas.toBlob((blob) => {
        if (!blob) {
          reject(new Error("The sample image could not be generated."));
          return;
        }
        resolve(new File([blob], filename, { type: "image/jpeg" }));
      }, "image/jpeg", 0.92);
    });
  }

  function drawSampleReceipt() {
    const canvas = document.createElement("canvas");
    canvas.width = 760;
    canvas.height = 1040;
    const ctx = canvas.getContext("2d");
    const ink = "#171613";
    const muted = "#5d5951";
    const rule = "#b9b2a4";

    ctx.fillStyle = "#f8f5ee";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = "#ddd7ca";
    ctx.lineWidth = 1;
    ctx.strokeRect(16.5, 16.5, canvas.width - 33, canvas.height - 33);

    ctx.fillStyle = ink;
    ctx.textAlign = "center";
    ctx.font = "600 30px 'Courier New', monospace";
    ctx.fillText("SHARMA TRADERS", canvas.width / 2, 80);
    ctx.font = "15px 'Courier New', monospace";
    ctx.fillStyle = muted;
    ctx.fillText("Wholesale Grocers & Distributors", canvas.width / 2, 106);
    ctx.fillText("14 Market Road, Bengaluru 560001", canvas.width / 2, 126);
    ctx.fillText("GSTIN: 29AAAAA0000A1Z5", canvas.width / 2, 146);

    ctx.fillStyle = ink;
    ctx.textAlign = "left";
    ctx.font = "600 15px 'Courier New', monospace";
    ctx.fillText("TAX INVOICE", 48, 190);
    ctx.font = "14px 'Courier New', monospace";
    ctx.fillText("Date: 02 Nov 2026", 48, 220);
    ctx.textAlign = "right";
    ctx.fillText("Invoice: ST-261105", canvas.width - 48, 220);

    ctx.strokeStyle = ink;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(48, 238);
    ctx.lineTo(canvas.width - 48, 238);
    ctx.stroke();

    ctx.lineWidth = 1;
    ctx.strokeStyle = rule;
    ctx.beginPath();
    ctx.moveTo(48, 266);
    ctx.lineTo(canvas.width - 48, 266);
    ctx.stroke();

    ctx.fillStyle = muted;
    ctx.font = "600 12px 'Courier New', monospace";
    ctx.textAlign = "left";
    ctx.fillText("ITEM", 48, 260);
    ctx.fillText("QTY", 430, 260);
    ctx.fillText("RATE", 530, 260);
    ctx.textAlign = "right";
    ctx.fillText("AMOUNT", canvas.width - 48, 260);

    const items = [
      ["BASMATI RICE 25 KG", "25 KG", "94.00", "2,350.00"],
      ["SURF EXCEL 1KG", "12 PKT", "128.00", "1,536.00"],
      ["HARPIC POWER PLUS 1L", "6 BTL", "96.00", "576.00"],
      ["PARLE-G GOLD 1KG", "10 PKT", "88.00", "880.00"],
      ["AMUL BUTTER 500G", "4 PCS", "298.00", "1,192.00"],
    ];

    let y = 302;
    ctx.fillStyle = ink;
    ctx.font = "14px 'Courier New', monospace";
    items.forEach((item) => {
      ctx.textAlign = "left";
      ctx.fillText(item[0], 48, y);
      ctx.fillText(item[1], 430, y);
      ctx.fillText(`₹${item[2]}`, 530, y);
      ctx.textAlign = "right";
      ctx.fillText(item[3], canvas.width - 48, y);
      ctx.strokeStyle = "#e7e1d5";
      ctx.beginPath();
      ctx.moveTo(48, y + 16);
      ctx.lineTo(canvas.width - 48, y + 16);
      ctx.stroke();
      y += 42;
    });

    ctx.strokeStyle = ink;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(48, y + 6);
    ctx.lineTo(canvas.width - 48, y + 6);
    ctx.stroke();

    ctx.font = "600 16px 'Courier New', monospace";
    ctx.textAlign = "left";
    ctx.fillText("SUBTOTAL", 48, y + 54);
    ctx.textAlign = "right";
    ctx.fillText("₹6,534.00", canvas.width - 48, y + 54);
    ctx.font = "600 22px 'Courier New', monospace";
    ctx.textAlign = "left";
    ctx.fillText("TOTAL", 48, y + 100);
    ctx.textAlign = "right";
    ctx.fillText("₹6,534.00", canvas.width - 48, y + 100);

    ctx.fillStyle = muted;
    ctx.font = "13px 'Courier New', monospace";
    ctx.textAlign = "center";
    ctx.fillText("Goods once sold will not be taken back.", canvas.width / 2, y + 168);
    ctx.fillText("Thank you for your business.", canvas.width / 2, y + 192);
    return canvas;
  }

  function drawNonReceipt() {
    const canvas = document.createElement("canvas");
    canvas.width = 760;
    canvas.height = 960;
    const ctx = canvas.getContext("2d");

    ctx.fillStyle = "#d8d2c5";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#c6bfb1";
    ctx.fillRect(60, 100, 640, 760);

    ctx.fillStyle = "#2b2925";
    ctx.fillRect(130, 170, 500, 620);

    ctx.fillStyle = "#e8e3d7";
    ctx.fillRect(162, 212, 436, 146);
    ctx.fillStyle = "#3e4b36";
    ctx.textAlign = "right";
    ctx.font = "600 44px 'Courier New', monospace";
    ctx.fillText("250 + 450", 574, 278);
    ctx.font = "600 30px 'Courier New', monospace";
    ctx.fillText("= 700", 574, 328);

    const labels = ["7", "8", "9", "÷", "4", "5", "6", "×", "1", "2", "3", "−", "0", ".", "=", "+"];
    ctx.textAlign = "center";
    ctx.font = "600 24px 'Courier New', monospace";
    labels.forEach((label, index) => {
      const col = index % 4;
      const row = Math.floor(index / 4);
      const x = 172 + col * 104;
      const y = 400 + row * 80;
      ctx.fillStyle = label === "=" ? "#a64a2c" : "#47443d";
      ctx.fillRect(x, y, 82, 58);
      ctx.fillStyle = "#f4f1e9";
      ctx.fillText(label, x + 41, y + 39);
    });

    ctx.fillStyle = "#5d5951";
    ctx.font = "14px 'Courier New', monospace";
    ctx.textAlign = "center";
    ctx.fillText("DESK CALCULATOR · 12 DIGIT", 380, 850);
    return canvas;
  }

  async function addSample(type) {
    try {
      const canvas = type === "receipt" ? drawSampleReceipt() : drawNonReceipt();
      const filename = type === "receipt" ? "sharma_traders_sample.jpg" : "calculator_photo.jpg";
      const file = await canvasToFile(canvas, filename);
      addFiles([file]);
    } catch (error) {
      showToast(error.message || "The sample image could not be generated.");
    }
  }

  // ── Dialog and navigation events ─────────────────────────────────────────

  function bindGlobalEvents() {
    $("nav-toggle").addEventListener("click", toggleMobileNav);
    $("nav-scrim").addEventListener("click", closeMobileNav);
    document.querySelectorAll("[data-route-link]").forEach((link) => {
      link.addEventListener("click", closeMobileNav);
    });
    window.addEventListener("hashchange", () => {
      document.querySelectorAll("dialog.drawer[open]").forEach((dialog) => dialog.close());
      renderRoute(currentRouteFromHash());
    });

    const desktop = window.matchMedia("(min-width: 1041px)");
    const handleDesktop = (event) => { if (event.matches) closeMobileNav(); };
    if (desktop.addEventListener) desktop.addEventListener("change", handleDesktop);
    else desktop.addListener(handleDesktop);

    $("refresh-btn").addEventListener("click", () => loadAll());
    $("offline-retry").addEventListener("click", () => loadAll());
    $("btn-scan").addEventListener("click", openScanDialog);

    document.addEventListener("click", (event) => {
      const trigger = event.target.closest("[data-close]");
      if (!trigger) return;
      const id = trigger.dataset.close;
      if (id === "scan-dialog") closeScanDialog();
      else closeDialog(id);
    });

    ["scan-dialog", "receipt-dialog", "product-dialog"].forEach((id) => {
      const dialog = $(id);
      dialog.addEventListener("click", (event) => {
        if (event.target !== dialog) return;
        if (id === "scan-dialog") closeScanDialog();
        else dialog.close();
      });
    });

    $("scan-dialog").addEventListener("cancel", (event) => {
      if (!state.scan.processing) return;
      event.preventDefault();
      showToast("Please wait for the current document to finish processing.");
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeMobileNav();
    });
  }

  function bindInventoryEvents() {
    $("inventory-search").addEventListener("input", (event) => {
      state.inventory.query = event.target.value;
      renderInventory();
    });
    $("inventory-sort").addEventListener("change", (event) => {
      state.inventory.sort = event.target.value;
      renderInventory();
    });
    $("inventory-categories").addEventListener("click", (event) => {
      const button = event.target.closest("[data-category]");
      if (!button) return;
      state.inventory.category = button.dataset.category;
      renderInventory();
    });

    activateRows($("inventory-body"), "product-id", (id) => openProduct(id));
    activateRows($("overview-inventory-body"), "product-id", (id) => openProduct(id));
    activateRows($("recent-receipts"), "receipt-id", (id) => openReceipt(id));
    activateRows($("ask-receipts"), "receipt-id", (id) => openReceipt(id));

    $("product-content").addEventListener("click", (event) => {
      const trigger = event.target.closest("[data-open-receipt]");
      if (!trigger) return;
      closeDialog("product-dialog");
      setTimeout(() => openReceipt(trigger.dataset.openReceipt), 80);
    });

    $("receipt-content").addEventListener("click", (event) => {
      const trigger = event.target.closest("[data-product-id]");
      if (!trigger) return;
      closeDialog("receipt-dialog");
      setTimeout(() => openProduct(trigger.dataset.productId), 80);
    });
  }

  function activateRows(container, dataKey, callback) {
    if (!container) return;
    const selector = `[data-${dataKey}]`;
    container.addEventListener("click", (event) => {
      const row = event.target.closest(selector);
      if (!row || event.target.closest("button:not([data-close])")) {
        if (!row) return;
      }
      callback(row.dataset[dataKey.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())]);
    });
    container.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const row = event.target.closest(selector);
      if (!row) return;
      event.preventDefault();
      callback(row.dataset[dataKey.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())]);
    });
  }

  function bindReceiptEvents() {
    $("receipts-search").addEventListener("input", (event) => {
      state.receiptFilter.query = event.target.value;
      renderReceipts();
    });
    $("receipts-month").addEventListener("change", (event) => {
      state.receiptFilter.month = event.target.value;
      renderReceipts();
    });
    $("export-receipts").addEventListener("click", exportReceipts);
    activateRows($("receipts-body"), "receipt-id", (id) => openReceipt(id));
  }

  function bindQueryEvents() {
    $("ask-form").addEventListener("submit", (event) => {
      event.preventDefault();
      runQuery($("ask-input").value);
    });

    $("quick-ask-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const input = $("quick-ask-input");
      const query = input.value.trim();
      if (!query) return;
      input.value = "";
      askQuestion(query);
    });

    document.querySelectorAll(".ask-example, .example-row").forEach((button) => {
      button.addEventListener("click", () => askQuestion(button.dataset.question));
    });
  }

  function bindScanEvents() {
    const dropzone = $("dropzone");
    const fileInput = $("file-input");

    dropzone.addEventListener("click", (event) => {
      if (event.target.closest("button")) return;
      fileInput.click();
    });
    dropzone.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        fileInput.click();
      }
    });

    fileInput.addEventListener("change", (event) => {
      addFiles(event.target.files);
      event.target.value = "";
    });

    ["dragenter", "dragover"].forEach((type) => {
      dropzone.addEventListener(type, (event) => {
        event.preventDefault();
        dropzone.classList.add("is-dragover");
      });
    });
    dropzone.addEventListener("dragleave", (event) => {
      if (!dropzone.contains(event.relatedTarget)) dropzone.classList.remove("is-dragover");
    });
    dropzone.addEventListener("drop", (event) => {
      event.preventDefault();
      dropzone.classList.remove("is-dragover");
      addFiles(event.dataTransfer.files);
    });

    $("sample-receipt").addEventListener("click", (event) => {
      event.stopPropagation();
      addSample("receipt");
    });
    $("sample-rejection").addEventListener("click", (event) => {
      event.stopPropagation();
      addSample("non-receipt");
    });

    $("staged-list").addEventListener("click", (event) => {
      const trigger = event.target.closest("[data-remove-file]");
      if (!trigger) return;
      removeScanFile(Number(trigger.dataset.removeFile));
    });

    const addFilesButton = $("scan-add-files");
    if (addFilesButton) addFilesButton.addEventListener("click", () => fileInput.click());

    $("scan-clear").addEventListener("click", () => {
      resetScanStage();
      dropzone.focus({ preventScroll: true });
    });

    document.querySelectorAll("[data-scan-mode]").forEach((button) => {
      button.addEventListener("click", () => setScanMode(button.dataset.scanMode));
    });

    $("scan-process").addEventListener("click", processScan);
    $("scan-another").addEventListener("click", () => {
      resetScanStage();
      dropzone.focus({ preventScroll: true });
    });
    $("batch-scan-another").addEventListener("click", () => {
      resetScanStage();
      dropzone.focus({ preventScroll: true });
    });
    $("batch-done").addEventListener("click", () => {
      closeScanDialog();
      resetScanStage();
    });

    $("scan-view-receipt").addEventListener("click", () => {
      const result = state.scan.lastResult;
      if (!result) return;
      const preview = state.scan.lastMode !== "persist" || !result.id;
      if (closeScanDialog()) {
        setTimeout(() => openReceiptData(result, { preview }), 60);
      }
    });

    $("scan-batch-body").addEventListener("click", (event) => {
      const trigger = event.target.closest("[data-batch-inspect]");
      if (!trigger || !state.scan.lastBatch) return;
      const index = Number(trigger.dataset.batchInspect);
      const item = (state.scan.lastBatch.results || [])[index];
      if (!item) return;
      const entry = item.receipt || item.extraction;
      if (!entry) return;
      const preview = state.scan.lastMode !== "persist" || !item.receipt;
      if (closeScanDialog()) {
        setTimeout(() => openReceiptData(entry, { preview }), 60);
      }
    });

    $("scan-error-retry").addEventListener("click", () => {
      $("scan-error").hidden = true;
      if (state.scan.errorRetry === "staged" && state.scan.files.length) {
        renderStaged();
      } else {
        resetScanStage();
        dropzone.focus({ preventScroll: true });
      }
    });
  }

  // ── Authentication ────────────────────────────────────────────────────────

  function setAuthConnection(state) {
    const dot = $("auth-db-dot");
    const label = $("auth-db-label");
    if (!dot || !label) return;
    dot.classList.toggle("is-offline", state !== "ready");
    if (state === "checking") label.textContent = "Checking ledger connection";
    else if (state === "offline") label.textContent = "Ledger service unreachable";
    else label.textContent = "Ledger database connected";
    $("auth-retry").hidden = state !== "offline";
  }

  function hideAuthError() {
    $("auth-error").hidden = true;
    $("auth-error").textContent = "";
  }

  function showAuthError(message) {
    $("auth-error").textContent = message;
    $("auth-error").hidden = false;
  }

  function setAuthMode(mode) {
    authState.mode = mode === "setup" ? "setup" : "login";
    const setup = authState.mode === "setup";
    $("auth-mode-label").textContent = setup ? "Access / First use" : "Access / Sign in";
    $("auth-title").innerHTML = setup
      ? 'Claim the <em>ledger<span class="accent">.</span></em>'
      : 'Welcome <em>back<span class="accent">.</span></em>';
    $("auth-copy").textContent = setup
      ? "No owner account exists yet. Create the first account to protect this purchasing record."
      : "Sign in to open the purchasing ledger for Meera Stores.";
    $("auth-confirm-field").hidden = !setup;
    $("auth-confirm").required = setup;
    $("auth-password").setAttribute("autocomplete", setup ? "new-password" : "current-password");
    $("auth-submit-label").textContent = setup ? "Create owner account" : "Enter ledger";
    $("auth-note").textContent = setup
      ? "The password is hashed before it reaches PostgreSQL. It cannot be read back from the database."
      : "One owner account · Sessions expire after 12 hours.";
    hideAuthError();
  }

  function showAuthScreen({ mode = "login", message = "", offline = false } = {}) {
    document.querySelectorAll("dialog[open]").forEach((dialog) => {
      try { dialog.close(); } catch { /* ignore */ }
    });
    $("app-shell").hidden = true;
    $("auth-screen").hidden = false;
    setAuthMode(mode);
    setAuthConnection(offline ? "offline" : "ready");
    if (message) showAuthError(message);
    const username = $("auth-username");
    if (username && !username.value.trim()) {
      setTimeout(() => username.focus({ preventScroll: true }), 80);
    }
  }

  function hideAuthScreen() {
    $("auth-screen").hidden = true;
    $("app-shell").hidden = false;
    toggleUserMenu(false);
  }

  function userInitials(name) {
    const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return "—";
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  function applyUser() {
    const user = authState.user;
    if (!user) return;
    const name = user.display_name || user.username || "Owner";
    $("user-initials").textContent = userInitials(name);
    $("user-name").textContent = name;
    $("user-meta").textContent = `@${user.username} · Owner`;
    $("sidebar-user").textContent = name;
    $("sidebar-user").title = `Signed in as ${name}`;
    $("user-btn").title = `${name} — account`;
  }

  function toggleUserMenu(force) {
    const menu = $("user-menu");
    const button = $("user-btn");
    if (!menu || !button) return;
    const open = typeof force === "boolean" ? force : menu.hidden;
    menu.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
  }

  function enterLedger() {
    hideAuthScreen();
    applyUser();
    if (!authState.booted) {
      authState.booted = true;
      bootLedger();
    } else {
      loadAll();
    }
  }

  async function checkSession() {
    setAuthConnection("checking");
    const form = $("auth-form");
    const submit = $("auth-submit");
    form.setAttribute("aria-busy", "true");
    submit.disabled = true;

    try {
      const status = await getJSON(API.authStatus);
      if (status.authenticated && status.user) {
        authState.user = status.user;
        enterLedger();
        return;
      }
      authState.user = null;
      showAuthScreen({ mode: status.setup_required ? "setup" : "login" });
    } catch (error) {
      authState.user = null;
      const staticPreview =
        window.location.protocol === "file:" ||
        error.status === 404 ||
        error.status === 405;
      showAuthScreen({
        mode: "login",
        message: staticPreview
          ? "This is a static preview, so sign-in cannot reach the server. Start the ledger and open http://127.0.0.1:8000 instead."
          : isDatabaseError(error)
            ? "The ledger database is offline. Start PostgreSQL, then retry."
            : errorText(error, "The ledger service is unreachable. Check the server and retry."),
        offline: true,
      });
    } finally {
      form.removeAttribute("aria-busy");
      submit.disabled = false;
    }
  }

  async function submitAuth(event) {
    event.preventDefault();
    if (authState.busy) return;

    const username = $("auth-username").value.trim();
    const password = $("auth-password").value;
    const confirm = $("auth-confirm").value;
    hideAuthError();

    if (!username) {
      showAuthError("Enter your username.");
      $("auth-username").focus();
      return;
    }
    if (!password) {
      showAuthError("Enter your password.");
      $("auth-password").focus();
      return;
    }
    if (authState.mode === "setup") {
      if (password.length < 8) {
        showAuthError("Use at least 8 characters for the password.");
        $("auth-password").focus();
        return;
      }
      if (password !== confirm) {
        showAuthError("The passwords do not match.");
        $("auth-confirm").focus();
        return;
      }
    }

    authState.busy = true;
    const submit = $("auth-submit");
    submit.disabled = true;
    $("auth-submit-label").textContent = authState.mode === "setup" ? "Creating account" : "Signing in";

    try {
      const endpoint = authState.mode === "setup" ? API.authSetup : API.authLogin;
      const user = await getJSON(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      authState.user = user;
      $("auth-password").value = "";
      $("auth-confirm").value = "";
      enterLedger();
    } catch (error) {
      if (error.status === 409) {
        setAuthMode("login");
        showAuthError("An owner account already exists. Sign in with those credentials.");
      } else if (error.status === 401) {
        showAuthError("Incorrect username or password.");
      } else if (isDatabaseError(error)) {
        setAuthConnection("offline");
        showAuthError("The ledger database is offline. Start PostgreSQL and try again.");
      } else {
        showAuthError(errorText(error, "Sign-in could not be completed."));
      }
      $("auth-password").value = "";
      $("auth-password").focus();
    } finally {
      authState.busy = false;
      submit.disabled = false;
      $("auth-submit-label").textContent = authState.mode === "setup" ? "Create owner account" : "Enter ledger";
    }
  }

  function bindAuthEvents() {
    $("auth-form").addEventListener("submit", submitAuth);
    ["auth-username", "auth-password", "auth-confirm"].forEach((id) => {
      $(id).addEventListener("input", hideAuthError);
    });
    $("auth-reveal").addEventListener("click", () => {
      const input = $("auth-password");
      const showing = input.type === "text";
      input.type = showing ? "password" : "text";
      $("auth-reveal").textContent = showing ? "Show" : "Hide";
      $("auth-reveal").setAttribute("aria-pressed", String(!showing));
    });
    $("auth-retry").addEventListener("click", checkSession);

    $("user-btn").addEventListener("click", (event) => {
      event.stopPropagation();
      toggleUserMenu();
    });

    document.addEventListener("click", (event) => {
      if (!$("user-menu").hidden && !event.target.closest(".user")) {
        toggleUserMenu(false);
      }
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !$("user-menu").hidden) {
        toggleUserMenu(false);
      }
    });
  }

  // ── Boot ──────────────────────────────────────────────────────────────────

  function renderAll() {
    renderOverview();
    renderAnalytics();
    renderInventory();
    renderReceipts();
    updateSyncUI();
  }

  function bootLedger() {
    bindGlobalEvents();
    bindInventoryEvents();
    bindReceiptEvents();
    bindQueryEvents();
    bindScanEvents();

    const hash = window.location.hash.replace(/^#\/?/, "");
    const route = ROUTES.includes(hash) ? hash : "overview";
    if (window.location.hash !== `#${route}`) {
      history.replaceState(null, "", `#${route}`);
    }
    renderRoute(route, { initial: true });
    setScanMode("persist");
    loadAll();
  }

  function init() {
    bindAuthEvents();
    checkSession();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // __APPEND__
})();
