export function getStatusValues(row) {
  const primaryStatus = row.status || "unresolved";
  const flags = Array.isArray(row.status_flags) ? row.status_flags : [];
  if (flags.includes("unit_mismatch")) return ["unit_mismatch"];
  return [...new Set([primaryStatus, ...flags].filter(Boolean))];
}
