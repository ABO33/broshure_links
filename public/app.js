const form = document.querySelector("#processForm");
const pdfInput = document.querySelector("#pdfInput");
const pdfLabel = document.querySelector("#pdfLabel");
const progress = document.querySelector("#progress");
const processButton = document.querySelector("#processButton");
const summaryGrid = document.querySelector("#summaryGrid");
const resultsBody = document.querySelector("#resultsBody");
const downloads = document.querySelector("#downloads");
const resultHint = document.querySelector("#resultHint");
const textFilterInputs = [...document.querySelectorAll(".column-filter[data-query-key]")];
const statusFilterButton = document.querySelector("#statusFilterButton");
const priceFilterButton = document.querySelector("#priceFilterButton");
const columnFilterMenu = document.querySelector("#columnFilterMenu");

let lastResult = null;
let activeFilter = null;

const tableState = {
  textFilters: {
    page: "",
    sku: "",
    box_type: "",
    brochure_price: "",
    website_price: "",
    url: ""
  },
  sort: { key: "", direction: "asc" },
  menuSearch: { status: "", price_status: "" },
  filters: { status: null, price_status: null }
};

const labels = {
  mapped: "Mapped",
  linked: "Linked",
  search: "Search",
  blocked: "Blocked",
  unresolved: "Unresolved",
  error: "Error",
  match: "Match",
  different: "Different",
  search_only: "Search only",
  no_url: "No URL",
  no_brochure_price: "No brochure price",
  no_website_price: "No website price",
  not_found: "Not found",
  not_checked: "Not checked",
  playwright_unavailable: "Browser unavailable",
  website_price_found: "Website price found"
};

const filterButtons = {
  status: statusFilterButton,
  price_status: priceFilterButton
};

const excelColumns = [
  ["page", "Page"],
  ["sku", "SKU"],
  ["status", "Status"],
  ["box_type", "Box"],
  ["brochure_price", "Brochure price"],
  ["website_price", "Website price"],
  ["price_status", "Price check"],
  ["url", "URL"]
];

pdfInput.addEventListener("change", () => {
  pdfLabel.textContent = pdfInput.files[0]?.name || "Choose brochure PDF";
});

textFilterInputs.forEach((input) => {
  input.addEventListener("input", () => {
    tableState.textFilters[input.dataset.queryKey] = input.value.trim();
    renderRows();
  });
});

form.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && event.target.matches(".column-filter")) {
    event.preventDefault();
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!pdfInput.files[0]) return;

  setBusy(true);
  closeColumnMenu();
  downloads.hidden = true;
  resultHint.textContent = form.elements.comparePrices.checked
    ? "Reading the PDF, checking Praktis euro prices, and writing links..."
    : "Reading text, detecting SKU boxes, and writing PDF links...";

  try {
    const response = await fetch("/api/process", {
      method: "POST",
      body: new FormData(form)
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Processing failed");

    lastResult = payload;
    resetTableState();
    renderSummary(payload.summary);
    renderRows();
    downloads.hidden = !payload.pdfBase64;
    resultHint.textContent = payload.summary.blockedLookups
      ? "Some exact live lookups were blocked; search fallback links were used where available."
      : "Linked PDF is ready.";
  } catch (error) {
    lastResult = null;
    resultsBody.innerHTML = `<tr class="empty"><td colspan="8">${escapeHtml(error.message)}</td></tr>`;
    resultHint.textContent = "Processing failed.";
  } finally {
    setBusy(false);
  }
});

document.querySelector("#downloadPdf").addEventListener("click", () => {
  if (!lastResult?.pdfBase64) return;
  downloadBlob(base64ToBlob(lastResult.pdfBase64, "application/pdf"), lastResult.outputFileName || "linked-brochure.pdf");
});

document.querySelector("#downloadExcel").addEventListener("click", () => {
  if (!lastResult?.rows) return;
  const rows = getVisibleRows();
  const blob = new Blob([toExcelHtml(rows)], { type: "application/vnd.ms-excel;charset=utf-8" });
  downloadBlob(blob, "brochure-link-report.xls");
});

document.querySelector("#downloadJson").addEventListener("click", () => {
  if (!lastResult) return;
  const json = JSON.stringify({ summary: lastResult.summary, rows: lastResult.rows }, null, 2);
  downloadBlob(new Blob([json], { type: "application/json" }), "brochure-link-report.json");
});

Object.entries(filterButtons).forEach(([key, button]) => {
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    if (activeFilter === key && !columnFilterMenu.hidden) {
      closeColumnMenu();
      return;
    }
    openColumnMenu(key, button);
  });
});

columnFilterMenu.addEventListener("click", (event) => {
  event.stopPropagation();
  const button = event.target.closest("button");
  if (!button || !activeFilter) return;

  if (button.dataset.sort) {
    tableState.sort = { key: activeFilter, direction: button.dataset.sort };
    renderRows();
    renderColumnMenu(activeFilter);
    return;
  }

  if (button.dataset.action === "clear-filter") {
    tableState.filters[activeFilter] = null;
    tableState.menuSearch[activeFilter] = "";
    renderRows();
    renderColumnMenu(activeFilter);
    return;
  }

  if (button.dataset.action === "close-filter") {
    closeColumnMenu();
  }
});

columnFilterMenu.addEventListener("input", (event) => {
  if (!activeFilter || !event.target.matches(".filter-search")) return;
  tableState.menuSearch[activeFilter] = event.target.value;
  renderColumnMenu(activeFilter, true);
});

columnFilterMenu.addEventListener("change", (event) => {
  if (!activeFilter || !event.target.matches("input[type='checkbox']")) return;

  const values = getAllFilterValues(activeFilter);
  const selected = getSelectedValues(activeFilter);
  const visibleValues = getVisibleMenuValues(activeFilter);

  if (event.target.dataset.action === "select-visible") {
    visibleValues.forEach((value) => {
      if (event.target.checked) selected.add(value);
      else selected.delete(value);
    });
  } else if (event.target.dataset.value) {
    if (event.target.checked) selected.add(event.target.dataset.value);
    else selected.delete(event.target.dataset.value);
  }

  tableState.filters[activeFilter] = selected.size === values.length ? null : selected;
  renderRows();
  renderColumnMenu(activeFilter);
});

document.addEventListener("click", (event) => {
  if (!columnFilterMenu.hidden && !columnFilterMenu.contains(event.target)) {
    closeColumnMenu();
  }
});

window.addEventListener("resize", closeColumnMenu);

function resetTableState() {
  Object.keys(tableState.textFilters).forEach((key) => {
    tableState.textFilters[key] = "";
  });
  tableState.sort = { key: "", direction: "asc" };
  tableState.menuSearch = { status: "", price_status: "" };
  tableState.filters = { status: null, price_status: null };
  textFilterInputs.forEach((input) => {
    input.value = "";
  });
  updateFilterButtons();
}

function setBusy(isBusy) {
  processButton.disabled = isBusy;
  processButton.textContent = isBusy ? "Processing..." : "Process PDF";
  progress.hidden = !isBusy;
}

function renderSummary(summary) {
  const values = [
    [summary.uniqueSkus, "SKUs"],
    [summary.linkedAnnotations, "Links"],
    [summary.unresolvedSkus, "Unresolved"],
    [summary.priceDifferent ?? 0, "Price diffs"],
    [summary.pages, "Pages"]
  ];
  summaryGrid.innerHTML = values
    .map(([value, label]) => `<div><strong>${value}</strong><span>${label}</span></div>`)
    .join("");
}

function renderRows() {
  updateFilterButtons();

  const rows = getVisibleRows();
  if (!lastResult) {
    resultsBody.innerHTML = `<tr class="empty"><td colspan="8">No file processed yet.</td></tr>`;
    return;
  }

  if (!lastResult?.rows?.length) {
    resultsBody.innerHTML = `<tr class="empty"><td colspan="8">No readable SKU codes were found.</td></tr>`;
    return;
  }

  if (!rows.length) {
    resultsBody.innerHTML = `<tr class="empty"><td colspan="8">No rows match the current filters.</td></tr>`;
    return;
  }

  resultsBody.innerHTML = rows.map(rowToHtml).join("");
}

function rowToHtml(row) {
  const status = getStatusValue(row);
  const priceStatus = getPriceStatusValue(row);
  const url = row.url
    ? `<a href="${escapeAttr(row.url)}" target="_blank" rel="noreferrer">${escapeHtml(row.title || row.url)}</a>`
    : `<span>${escapeHtml(row.message || "No link")}</span>`;

  return `
    <tr>
      <td>${row.page}</td>
      <td>${escapeHtml(row.sku)}</td>
      <td>${badgeHtml(status)}</td>
      <td>${escapeHtml(row.box_type)}</td>
      <td>${formatPrice(row.brochure_price)}</td>
      <td>${formatPrice(row.website_price)}</td>
      <td title="${escapeAttr(row.price_message || "")}">${priceStatus === "not_checked" ? `<span class="muted-cell">Not checked</span>` : badgeHtml(priceStatus)}</td>
      <td class="url-cell">${url}</td>
    </tr>
  `;
}

function getVisibleRows() {
  let rows = [...(lastResult?.rows || [])];

  for (const [key, value] of Object.entries(tableState.textFilters)) {
    const query = value.toLowerCase();
    if (!query) continue;
    rows = rows.filter((row) => getTextFilterValue(row, key).toLowerCase().includes(query));
  }

  for (const key of ["status", "price_status"]) {
    const selected = tableState.filters[key];
    if (selected) {
      rows = rows.filter((row) => selected.has(getFilterValue(row, key)));
    }
  }

  if (tableState.sort.key) {
    rows.sort((left, right) => compareRows(left, right, tableState.sort.key, tableState.sort.direction));
  }

  return rows;
}

function getTextFilterValue(row, key) {
  if (key === "brochure_price" || key === "website_price") return formatPrice(row[key]);
  if (key === "url") return [row.url, row.title, row.message].filter(Boolean).join(" ");
  return String(row[key] ?? "");
}

function compareRows(left, right, key, direction) {
  const leftText = getFilterLabel(key, getFilterValue(left, key)).toLowerCase();
  const rightText = getFilterLabel(key, getFilterValue(right, key)).toLowerCase();
  const textCompare = leftText.localeCompare(rightText, undefined, { numeric: true, sensitivity: "base" });
  const fallback = Number(left.page || 0) - Number(right.page || 0) || String(left.sku || "").localeCompare(String(right.sku || ""));
  const result = textCompare || fallback;
  return direction === "desc" ? -result : result;
}

function openColumnMenu(key, button) {
  activeFilter = key;
  renderColumnMenu(key);
  columnFilterMenu.hidden = false;

  const rect = button.getBoundingClientRect();
  const left = Math.min(rect.left, window.innerWidth - 260);
  columnFilterMenu.style.left = `${Math.max(12, left)}px`;
  columnFilterMenu.style.top = `${rect.bottom + 8}px`;

  Object.entries(filterButtons).forEach(([name, item]) => {
    item.setAttribute("aria-expanded", String(name === key));
  });

  const searchInput = columnFilterMenu.querySelector(".filter-search");
  searchInput?.focus();
}

function closeColumnMenu() {
  activeFilter = null;
  columnFilterMenu.hidden = true;
  Object.values(filterButtons).forEach((button) => button.setAttribute("aria-expanded", "false"));
}

function renderColumnMenu(key, keepFocus = false) {
  const values = getAllFilterValues(key);
  const visibleValues = getVisibleMenuValues(key);
  const selected = getSelectedValues(key);
  const allVisibleSelected = visibleValues.length > 0 && visibleValues.every((value) => selected.has(value));
  const sortActive = tableState.sort.key === key ? tableState.sort.direction : "";

  columnFilterMenu.innerHTML = `
    <div class="menu-sort">
      <button type="button" data-sort="asc" class="${sortActive === "asc" ? "active" : ""}">Sort A to Z</button>
      <button type="button" data-sort="desc" class="${sortActive === "desc" ? "active" : ""}">Sort Z to A</button>
    </div>
    <input class="filter-search" type="search" placeholder="Search" value="${escapeAttr(tableState.menuSearch[key])}" autocomplete="off">
    <label class="check-row select-all">
      <input type="checkbox" data-action="select-visible" ${allVisibleSelected ? "checked" : ""}>
      <span>(Select All)</span>
    </label>
    <div class="filter-options">
      ${
        visibleValues.length
          ? visibleValues
              .map(
                (value) => `
                  <label class="check-row">
                    <input type="checkbox" data-value="${escapeAttr(value)}" ${selected.has(value) ? "checked" : ""}>
                    <span>${escapeHtml(getFilterLabel(key, value))}</span>
                  </label>
                `
              )
              .join("")
          : `<div class="filter-empty">No values</div>`
      }
    </div>
    <div class="menu-actions">
      <button type="button" data-action="clear-filter">Clear filter</button>
      <button type="button" data-action="close-filter">Close</button>
    </div>
  `;

  if (keepFocus) {
    const searchInput = columnFilterMenu.querySelector(".filter-search");
    searchInput?.focus();
    searchInput?.setSelectionRange(searchInput.value.length, searchInput.value.length);
  }
}

function getAllFilterValues(key) {
  const rows = lastResult?.rows || [];
  const values = new Set(rows.map((row) => getFilterValue(row, key)));
  return [...values].sort((left, right) =>
    getFilterLabel(key, left).localeCompare(getFilterLabel(key, right), undefined, { sensitivity: "base" })
  );
}

function getVisibleMenuValues(key) {
  const search = tableState.menuSearch[key].trim().toLowerCase();
  const values = getAllFilterValues(key);
  if (!search) return values;
  return values.filter((value) => getFilterLabel(key, value).toLowerCase().includes(search));
}

function getSelectedValues(key) {
  const allValues = getAllFilterValues(key);
  const saved = tableState.filters[key];
  if (!saved) return new Set(allValues);
  return new Set([...saved].filter((value) => allValues.includes(value)));
}

function getFilterValue(row, key) {
  if (key === "status") return getStatusValue(row);
  if (key === "price_status") return getPriceStatusValue(row);
  return "";
}

function getStatusValue(row) {
  return row.status || "unresolved";
}

function getPriceStatusValue(row) {
  return row.price_status || "not_checked";
}

function getFilterLabel(key, value) {
  return labels[value] || value || (key === "price_status" ? "Not checked" : "Blank");
}

function updateFilterButtons() {
  Object.entries(filterButtons).forEach(([key, button]) => {
    const allValues = getAllFilterValues(key);
    const selected = getSelectedValues(key);
    const filterActive = tableState.filters[key] !== null && selected.size !== allValues.length;
    const sortActive = tableState.sort.key === key;
    const value = button.querySelector(".filter-value");
    const mark = button.querySelector(".filter-mark");
    const marks = [];

    value.textContent = filterActive ? `${selected.size} selected` : "All";
    if (sortActive) marks.push(tableState.sort.direction === "asc" ? "A-Z" : "Z-A");

    button.classList.toggle("active", filterActive || sortActive);
    mark.textContent = marks.join(" ");
  });
}

function badgeHtml(value) {
  return `<span class="badge ${escapeAttr(value)}">${escapeHtml(labels[value] || value)}</span>`;
}

function toExcelHtml(rows) {
  const headerHtml = excelColumns.map(([, label]) => `<th>${escapeHtml(label)}</th>`).join("");
  const bodyHtml = rows
    .map(
      (row) => `
        <tr>
          <td>${row.page ?? ""}</td>
          <td>${escapeHtml(row.sku)}</td>
          <td>${excelBadgeHtml(getStatusValue(row))}</td>
          <td>${escapeHtml(row.box_type)}</td>
          <td class="number">${formatPrice(row.brochure_price)}</td>
          <td class="number">${formatPrice(row.website_price)}</td>
          <td>${getPriceStatusValue(row) === "not_checked" ? `<span class="muted-cell">Not checked</span>` : excelBadgeHtml(getPriceStatusValue(row))}</td>
          <td>${excelUrlHtml(row)}</td>
        </tr>
      `
    )
    .join("");

  return `<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <style>
      table { border-collapse: collapse; font-family: Arial, sans-serif; font-size: 12px; }
      th { background: #f3f6f8; color: #44525d; font-weight: 700; text-transform: uppercase; }
      th, td { border: 1px solid #d8e1e7; padding: 8px 10px; vertical-align: top; }
      .number { mso-number-format: "0.00"; }
      .badge { font-weight: 700; border-radius: 6px; padding: 3px 8px; display: inline-block; }
      .ok { color: #177a55; background: #e7f3ee; }
      .warn { color: #995e00; background: #f7eddf; }
      .bad { color: #a73333; background: #f6e7e7; }
      .muted-cell { color: #64727d; }
      a { color: #245fe0; }
    </style>
  </head>
  <body>
    <table>
      <thead><tr>${headerHtml}</tr></thead>
      <tbody>${bodyHtml}</tbody>
    </table>
  </body>
</html>`;
}

function excelBadgeHtml(value) {
  return `<span class="badge ${excelBadgeTone(value)}">${escapeHtml(labels[value] || value)}</span>`;
}

function excelBadgeTone(value) {
  if (["mapped", "linked", "match", "website_price_found"].includes(value)) return "ok";
  if (["error", "unresolved", "different"].includes(value)) return "bad";
  return "warn";
}

function excelUrlHtml(row) {
  if (!row.url) return escapeHtml(row.message || "No link");
  return `<a href="${escapeAttr(row.url)}">${escapeHtml(row.title || row.url)}</a>`;
}

function base64ToBlob(base64, type) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new Blob([bytes], { type });
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function formatPrice(value) {
  if (value === null || value === undefined || value === "") return "";
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  return number.toFixed(2);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}
