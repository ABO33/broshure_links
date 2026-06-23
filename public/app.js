const form = document.querySelector("#processForm");
const pdfInput = document.querySelector("#pdfInput");
const mappingInput = document.querySelector("#mappingInput");
const pdfLabel = document.querySelector("#pdfLabel");
const mappingLabel = document.querySelector("#mappingLabel");
const healthPill = document.querySelector("#healthPill");
const progress = document.querySelector("#progress");
const processButton = document.querySelector("#processButton");
const summaryGrid = document.querySelector("#summaryGrid");
const resultsBody = document.querySelector("#resultsBody");
const downloads = document.querySelector("#downloads");
const resultHint = document.querySelector("#resultHint");

let lastResult = null;

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
  playwright_unavailable: "Browser unavailable",
  website_price_found: "Website price found"
};

pdfInput.addEventListener("change", () => {
  pdfLabel.textContent = pdfInput.files[0]?.name || "Choose brochure PDF";
});

mappingInput.addEventListener("change", () => {
  mappingLabel.textContent = mappingInput.files[0]?.name || "Optional CSV or JSON with sku,url,title.";
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!pdfInput.files[0]) return;

  setBusy(true);
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
    renderSummary(payload.summary);
    renderRows(payload.rows);
    downloads.hidden = !payload.pdfBase64;
    resultHint.textContent = payload.summary.blockedLookups
      ? "Some exact live lookups were blocked; search fallback or mapping links were used where available."
      : "Linked PDF is ready.";
  } catch (error) {
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

document.querySelector("#downloadCsv").addEventListener("click", () => {
  if (!lastResult?.rows) return;
  downloadBlob(new Blob([toCsv(lastResult.rows)], { type: "text/csv;charset=utf-8" }), "brochure-link-report.csv");
});

document.querySelector("#downloadJson").addEventListener("click", () => {
  if (!lastResult) return;
  const json = JSON.stringify({ summary: lastResult.summary, rows: lastResult.rows }, null, 2);
  downloadBlob(new Blob([json], { type: "application/json" }), "brochure-link-report.json");
});

async function checkHealth() {
  try {
    const response = await fetch("/api/health");
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error("offline");
    healthPill.textContent = "Python backend ready";
    healthPill.className = "pill ok";
  } catch {
    healthPill.textContent = "Backend offline";
    healthPill.className = "pill bad";
  }
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

function renderRows(rows) {
  if (!rows.length) {
    resultsBody.innerHTML = `<tr class="empty"><td colspan="8">No readable SKU codes were found.</td></tr>`;
    return;
  }

  resultsBody.innerHTML = rows
    .map((row) => {
      const status = row.status || "unresolved";
      const url = row.url
        ? `<a href="${escapeAttr(row.url)}" target="_blank" rel="noreferrer">${escapeHtml(row.title || row.url)}</a>`
        : `<span>${escapeHtml(row.message || "No link")}</span>`;
      const priceStatus = row.price_status || "";
      const priceBadge = priceStatus
        ? `<span class="badge ${priceStatus}">${labels[priceStatus] || escapeHtml(priceStatus)}</span>`
        : `<span class="muted-cell">Not checked</span>`;
      return `
        <tr>
          <td>${row.page}</td>
          <td>${escapeHtml(row.sku)}</td>
          <td><span class="badge ${status}">${labels[status] || escapeHtml(status)}</span></td>
          <td>${escapeHtml(row.box_type)}</td>
          <td>${formatPrice(row.brochure_price)}</td>
          <td>${formatPrice(row.website_price)}</td>
          <td title="${escapeAttr(row.price_message || "")}">${priceBadge}</td>
          <td class="url-cell">${url}</td>
        </tr>
      `;
    })
    .join("");
}

function toCsv(rows) {
  const headers = [
    "page",
    "sku",
    "status",
    "box_type",
    "brochure_price",
    "website_price",
    "price_status",
    "price_message",
    "url",
    "title",
    "message"
  ];
  const escape = (value) => {
    const text = String(value ?? "");
    return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  };
  return [headers.join(","), ...rows.map((row) => headers.map((key) => escape(row[key])).join(","))].join("\n");
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

checkHealth();
