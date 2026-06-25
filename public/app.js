const form = document.querySelector("#processForm");
const pdfInput = document.querySelector("#pdfInput");
const pdfLabel = document.querySelector("#pdfLabel");
const excelInput = document.querySelector("#excelInput");
const excelLabel = document.querySelector("#excelLabel");
const modeInputs = [...document.querySelectorAll("input[name='mode']")];
const pageModeInputs = [...document.querySelectorAll("input[name='pageMode']")];
const pageNumberInput = document.querySelector("#pageNumberInput");
const progress = document.querySelector("#progress");
const processButton = document.querySelector("#processButton");
const summaryGrid = document.querySelector("#summaryGrid");
const resultsBody = document.querySelector("#resultsBody");
const downloads = document.querySelector("#downloads");
const resultHint = document.querySelector("#resultHint");
const textFilterInputs = [...document.querySelectorAll(".column-filter[data-query-key]")];
const columnFilterMenu = document.querySelector("#columnFilterMenu");
const choiceFilterKeys = ["status", "price_status", "excel_status", "triple_status"];
const filterButtons = Object.fromEntries(
  [...document.querySelectorAll(".filter-trigger[data-filter]")].map((button) => [button.dataset.filter, button])
);

let lastResult = null;
let activeFilter = null;

const tableState = {
  textFilters: {
    page: "",
    sku: "",
    box_type: "",
    brochure_price: "",
    website_price: "",
    excel_price: "",
    url: ""
  },
  sort: { key: "", direction: "asc" },
  menuSearch: Object.fromEntries(choiceFilterKeys.map((key) => [key, ""])),
  filters: Object.fromEntries(choiceFilterKeys.map((key) => [key, null]))
};

const labels = {
  mapped: "Mapped",
  linked: "Linked",
  search: "Search",
  search_only: "Search only",
  link_not_found: "Link not found",
  price_only: "Price only",
  blocked: "Blocked",
  unresolved: "Unresolved",
  error: "Error",
  match: "Match",
  different: "Different",
  no_url: "No URL",
  no_brochure_price: "No brochure price",
  no_website_price: "No website price",
  no_excel_price: "No Excel price",
  not_found: "Not found",
  not_checked: "Not checked",
  playwright_unavailable: "Browser unavailable",
  website_price_found: "Website price found"
};

const excelColumns = [
  ["page", "Page"],
  ["sku", "SKU"],
  ["status", "Status"],
  ["box_type", "Box"],
  ["brochure_price", "Brochure price"],
  ["website_price", "Website price"],
  ["excel_price", "Excel price"],
  ["price_status", "Website check"],
  ["excel_status", "Excel check"],
  ["triple_status", "Triple check"],
  ["url", "URL"]
];

pdfInput.addEventListener("change", () => {
  pdfLabel.textContent = pdfInput.files[0]?.name || "Choose brochure PDF";
});

excelInput.addEventListener("change", () => {
  excelLabel.textContent = excelInput.files[0]?.name || "Choose price Excel";
});

modeInputs.forEach((input) => input.addEventListener("change", updateModeState));
pageModeInputs.forEach((input) => input.addEventListener("change", updatePageScopeState));
updateModeState();
updatePageScopeState();

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
  if (modeNeedsExcel(getSelectedMode()) && !excelInput.files[0]) {
    resultHint.textContent = "Upload an Excel .xlsx file for the selected price check mode.";
    excelInput.focus();
    return;
  }
  if (getSelectedPageMode() === "single" && !pageNumberInput.value) {
    resultHint.textContent = "Enter the page number you want to process.";
    pageNumberInput.focus();
    return;
  }

  setBusy(true);
  closeColumnMenu();
  downloads.hidden = true;
  resultHint.textContent = processingMessage(getSelectedMode());

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
      : completionMessage(payload.summary.mode);
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
  downloadBlob(toXlsxBlob(rows), "brochure-link-report.xlsx");
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
  tableState.menuSearch = Object.fromEntries(choiceFilterKeys.map((key) => [key, ""]));
  tableState.filters = Object.fromEntries(choiceFilterKeys.map((key) => [key, null]));
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

function updateModeState() {
  const needsExcel = modeNeedsExcel(getSelectedMode());
  excelInput.required = needsExcel;
  excelInput.closest(".dropzone").classList.toggle("is-required", needsExcel);
}

function updatePageScopeState() {
  const singlePage = getSelectedPageMode() === "single";
  pageNumberInput.disabled = !singlePage;
  pageNumberInput.required = singlePage;
}

function getSelectedMode() {
  return modeInputs.find((input) => input.checked)?.value || "fallback_links";
}

function getSelectedPageMode() {
  return pageModeInputs.find((input) => input.checked)?.value || "all";
}

function modeNeedsExcel(mode) {
  return mode === "excel_prices" || mode === "full_check";
}

function processingMessage(mode) {
  if (mode === "website_links_prices") return "Reading the PDF, checking Praktis links and euro prices...";
  if (mode === "excel_prices") return "Reading the PDF and comparing brochure prices with Excel...";
  if (mode === "full_check") return "Reading the PDF, checking Praktis links, and comparing PDF, Excel, and website prices...";
  return "Reading text, detecting SKU boxes, and writing SKU search links...";
}

function completionMessage(mode) {
  if (mode === "excel_prices") return "Excel price report is ready.";
  if (mode === "full_check") return "Linked PDF and triple price report are ready.";
  if (mode === "website_links_prices") return "Linked PDF and website price report are ready.";
  return "Search-link PDF is ready.";
}

function renderSummary(summary) {
  const values = [
    [summary.uniqueSkus, "SKUs"],
    [summary.variantRows ?? 0, "Variants"],
    [summary.linkedAnnotations, "Links"],
    [summary.priceDifferent ?? 0, "Website diffs"],
    [summary.excelDifferent ?? 0, "Excel diffs"],
    [summary.tripleDifferent ?? 0, "Triple diffs"],
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
    resultsBody.innerHTML = `<tr class="empty"><td colspan="11">No file processed yet.</td></tr>`;
    return;
  }

  if (!lastResult?.rows?.length) {
    resultsBody.innerHTML = `<tr class="empty"><td colspan="11">No readable SKU codes were found.</td></tr>`;
    return;
  }

  if (!rows.length) {
    resultsBody.innerHTML = `<tr class="empty"><td colspan="11">No rows match the current filters.</td></tr>`;
    return;
  }

  resultsBody.innerHTML = rows.map(rowToHtml).join("");
}

function rowToHtml(row) {
  const status = getStatusValue(row);
  const priceStatus = getChoiceStatusValue(row, "price_status");
  const excelStatus = getChoiceStatusValue(row, "excel_status");
  const tripleStatus = getChoiceStatusValue(row, "triple_status");
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
      <td>${formatPrice(row.excel_price)}</td>
      <td title="${escapeAttr(row.price_message || "")}">${priceStatus === "not_checked" ? `<span class="muted-cell">Not checked</span>` : badgeHtml(priceStatus)}</td>
      <td title="${escapeAttr(row.excel_message || "")}">${excelStatus === "not_checked" ? `<span class="muted-cell">Not checked</span>` : badgeHtml(excelStatus)}</td>
      <td title="${escapeAttr(row.triple_message || "")}">${tripleStatus === "not_checked" ? `<span class="muted-cell">Not checked</span>` : badgeHtml(tripleStatus)}</td>
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

  for (const key of choiceFilterKeys) {
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
  if (key === "brochure_price" || key === "website_price" || key === "excel_price") return formatPrice(row[key]);
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
  if (choiceFilterKeys.includes(key)) return getChoiceStatusValue(row, key);
  return "";
}

function getStatusValue(row) {
  return row.status || "unresolved";
}

function getChoiceStatusValue(row, key) {
  return row[key] || "not_checked";
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

function toXlsxBlob(rows) {
  const hyperlinks = [];
  const sheetXml = buildWorksheetXml(rows, hyperlinks);
  const files = [
    ["[Content_Types].xml", contentTypesXml()],
    ["_rels/.rels", rootRelsXml()],
    ["docProps/app.xml", appPropsXml()],
    ["docProps/core.xml", corePropsXml()],
    ["xl/workbook.xml", workbookXml()],
    ["xl/_rels/workbook.xml.rels", workbookRelsXml()],
    ["xl/styles.xml", stylesXml()],
    ["xl/worksheets/sheet1.xml", sheetXml]
  ];

  if (hyperlinks.length) {
    files.push(["xl/worksheets/_rels/sheet1.xml.rels", worksheetRelsXml(hyperlinks)]);
  }

  return new Blob([zipStore(files)], {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
  });
}

function buildWorksheetXml(rows, hyperlinks) {
  const header = excelColumns.map(([, label], index) => cell(columnName(index + 1), 1, label, 1)).join("");
  const body = rows
    .map((row, index) => {
      const rowNumber = index + 2;
      const cells = excelColumns
        .map(([key], columnIndex) => excelCellForColumn(key, columnIndex + 1, rowNumber, row, hyperlinks))
        .join("");

      if (row.url) {
        hyperlinks.push({ ref: `${columnName(excelColumns.length)}${rowNumber}`, target: row.url });
      }

      return `<row r="${rowNumber}" ht="24" customHeight="1">${cells}</row>`;
    })
    .join("");
  const hyperlinkXml = hyperlinks.length
    ? `<hyperlinks>${hyperlinks.map((link, index) => `<hyperlink ref="${link.ref}" r:id="rId${index + 1}"/>`).join("")}</hyperlinks>`
    : "";

  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheetViews><sheetView workbookViewId="0" showGridLines="1"/></sheetViews>
  <sheetFormatPr defaultRowHeight="18"/>
  <cols>
    <col min="1" max="1" width="9" customWidth="1"/>
    <col min="2" max="2" width="14" customWidth="1"/>
    <col min="3" max="3" width="16" customWidth="1"/>
    <col min="4" max="4" width="12" customWidth="1"/>
    <col min="5" max="7" width="16" customWidth="1"/>
    <col min="8" max="10" width="18" customWidth="1"/>
    <col min="11" max="11" width="46" customWidth="1"/>
  </cols>
  <sheetData>
    <row r="1" ht="24" customHeight="1">${header}</row>
    ${body}
  </sheetData>
  ${hyperlinkXml}
</worksheet>`;
}

function excelCellForColumn(key, columnIndex, rowNumber, row) {
  const column = columnName(columnIndex);
  if (key === "page") return numberCell(column, rowNumber, row.page, 2);
  if (["brochure_price", "website_price", "excel_price"].includes(key)) {
    return numberCell(column, rowNumber, row[key], 3);
  }
  if (key === "status") {
    const status = getStatusValue(row);
    return cell(column, rowNumber, labels[status] || status, excelStatusStyle(status));
  }
  if (["price_status", "excel_status", "triple_status"].includes(key)) {
    const status = getChoiceStatusValue(row, key);
    return cell(
      column,
      rowNumber,
      status === "not_checked" ? "Not checked" : labels[status] || status,
      status === "not_checked" ? 7 : excelStatusStyle(status)
    );
  }
  if (key === "url") {
    return cell(column, rowNumber, row.url ? row.title || row.url : row.message || "No link", row.url ? 8 : 7);
  }
  return cell(column, rowNumber, row[key], 2);
}

function cell(column, row, value, style) {
  return `<c r="${column}${row}" s="${style}" t="inlineStr"><is><t>${xmlText(value)}</t></is></c>`;
}

function numberCell(column, row, value, style) {
  const number = Number(value);
  if (!Number.isFinite(number)) return cell(column, row, "", 2);
  return `<c r="${column}${row}" s="${style}"><v>${number}</v></c>`;
}

function excelStatusStyle(value) {
  if (["mapped", "linked", "match", "website_price_found"].includes(value)) return 4;
  if (["error", "unresolved", "different"].includes(value)) return 6;
  return 5;
}

function columnName(index) {
  let name = "";
  while (index > 0) {
    const remainder = (index - 1) % 26;
    name = String.fromCharCode(65 + remainder) + name;
    index = Math.floor((index - 1) / 26);
  }
  return name;
}

function contentTypesXml() {
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>`;
}

function rootRelsXml() {
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>`;
}

function appPropsXml() {
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Praktis Brochure Linker</Application>
</Properties>`;
}

function corePropsXml() {
  const created = new Date().toISOString();
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>Brochure link report</dc:title>
  <dc:creator>Praktis Brochure Linker</dc:creator>
  <cp:lastModifiedBy>Praktis Brochure Linker</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">${created}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">${created}</dcterms:modified>
</cp:coreProperties>`;
}

function workbookXml() {
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Report" sheetId="1" r:id="rId1"/></sheets>
</workbook>`;
}

function workbookRelsXml() {
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>`;
}

function worksheetRelsXml(hyperlinks) {
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  ${hyperlinks
    .map(
      (link, index) =>
        `<Relationship Id="rId${index + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="${xmlAttr(link.target)}" TargetMode="External"/>`
    )
    .join("")}
</Relationships>`;
}

function stylesXml() {
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="7">
    <font><sz val="11"/><color rgb="FF18232B"/><name val="Arial"/></font>
    <font><b/><sz val="11"/><color rgb="FF44525D"/><name val="Arial"/></font>
    <font><b/><sz val="11"/><color rgb="FF177A55"/><name val="Arial"/></font>
    <font><b/><sz val="11"/><color rgb="FF995E00"/><name val="Arial"/></font>
    <font><b/><sz val="11"/><color rgb="FFA73333"/><name val="Arial"/></font>
    <font><u/><sz val="11"/><color rgb="FF245FE0"/><name val="Arial"/></font>
    <font><sz val="11"/><color rgb="FF64727D"/><name val="Arial"/></font>
  </fonts>
  <fills count="6">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFF3F6F8"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFE7F3EE"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFF7EDDF"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFF6E7E7"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border><left style="thin"><color rgb="FFD8E1E7"/></left><right style="thin"><color rgb="FFD8E1E7"/></right><top style="thin"><color rgb="FFD8E1E7"/></top><bottom style="thin"><color rgb="FFD8E1E7"/></bottom><diagonal/></border>
  </borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="9">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"><alignment vertical="top"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"><alignment vertical="top" wrapText="1"/></xf>
    <xf numFmtId="2" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"><alignment vertical="top"/></xf>
    <xf numFmtId="0" fontId="2" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"><alignment vertical="top"/></xf>
    <xf numFmtId="0" fontId="3" fillId="4" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"><alignment vertical="top"/></xf>
    <xf numFmtId="0" fontId="4" fillId="5" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"><alignment vertical="top"/></xf>
    <xf numFmtId="0" fontId="6" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1"><alignment vertical="top"/></xf>
    <xf numFmtId="0" fontId="5" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1"><alignment vertical="top" wrapText="1"/></xf>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
  <dxfs count="0"/>
  <tableStyles count="0" defaultTableStyle="TableStyleMedium2" defaultPivotStyle="PivotStyleLight16"/>
</styleSheet>`;
}

function zipStore(files) {
  const encoder = new TextEncoder();
  const localParts = [];
  const centralParts = [];
  let offset = 0;

  for (const [name, content] of files) {
    const nameBytes = encoder.encode(name);
    const data = typeof content === "string" ? encoder.encode(content) : content;
    const crc = crc32(data);
    const localHeader = zipLocalHeader(nameBytes, data.length, crc);
    localParts.push(localHeader, nameBytes, data);
    centralParts.push(zipCentralHeader(nameBytes, data.length, crc, offset), nameBytes);
    offset += localHeader.length + nameBytes.length + data.length;
  }

  const centralSize = centralParts.reduce((sum, part) => sum + part.length, 0);
  const end = zipEndRecord(files.length, centralSize, offset);
  return concatUint8([...localParts, ...centralParts, end]);
}

function zipLocalHeader(nameBytes, size, crc) {
  const header = new Uint8Array(30);
  const view = new DataView(header.buffer);
  view.setUint32(0, 0x04034b50, true);
  view.setUint16(4, 20, true);
  view.setUint16(6, 0x0800, true);
  view.setUint16(8, 0, true);
  view.setUint16(10, 0, true);
  view.setUint16(12, 0, true);
  view.setUint32(14, crc, true);
  view.setUint32(18, size, true);
  view.setUint32(22, size, true);
  view.setUint16(26, nameBytes.length, true);
  view.setUint16(28, 0, true);
  return header;
}

function zipCentralHeader(nameBytes, size, crc, offset) {
  const header = new Uint8Array(46);
  const view = new DataView(header.buffer);
  view.setUint32(0, 0x02014b50, true);
  view.setUint16(4, 20, true);
  view.setUint16(6, 20, true);
  view.setUint16(8, 0x0800, true);
  view.setUint16(10, 0, true);
  view.setUint16(12, 0, true);
  view.setUint16(14, 0, true);
  view.setUint32(16, crc, true);
  view.setUint32(20, size, true);
  view.setUint32(24, size, true);
  view.setUint16(28, nameBytes.length, true);
  view.setUint16(30, 0, true);
  view.setUint16(32, 0, true);
  view.setUint16(34, 0, true);
  view.setUint16(36, 0, true);
  view.setUint32(38, 0, true);
  view.setUint32(42, offset, true);
  return header;
}

function zipEndRecord(count, centralSize, centralOffset) {
  const end = new Uint8Array(22);
  const view = new DataView(end.buffer);
  view.setUint32(0, 0x06054b50, true);
  view.setUint16(4, 0, true);
  view.setUint16(6, 0, true);
  view.setUint16(8, count, true);
  view.setUint16(10, count, true);
  view.setUint32(12, centralSize, true);
  view.setUint32(16, centralOffset, true);
  view.setUint16(20, 0, true);
  return end;
}

function concatUint8(parts) {
  const total = parts.reduce((sum, part) => sum + part.length, 0);
  const output = new Uint8Array(total);
  let offset = 0;
  for (const part of parts) {
    output.set(part, offset);
    offset += part.length;
  }
  return output;
}

function crc32(data) {
  let crc = 0xffffffff;
  for (const byte of data) {
    crc = CRC32_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

const CRC32_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let index = 0; index < 256; index += 1) {
    let value = index;
    for (let bit = 0; bit < 8; bit += 1) {
      value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    }
    table[index] = value >>> 0;
  }
  return table;
})();

function xmlText(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function xmlAttr(value) {
  return xmlText(value).replaceAll('"', "&quot;").replaceAll("'", "&apos;");
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
  window.setTimeout(() => URL.revokeObjectURL(url), 30000);
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
