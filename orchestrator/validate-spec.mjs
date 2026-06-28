export function validateFurnitureSpec(spec) {
  const errors = [];
  requireString(spec, "id", errors);
  requireString(spec, "type", errors);
  requireString(spec, "title", errors);
  requireNumber(spec?.dimensions, "widthMm", errors, "dimensions");
  requireNumber(spec?.dimensions, "depthMm", errors, "dimensions");
  requireNumber(spec?.dimensions, "heightMm", errors, "dimensions");
  if (!spec?.parts || typeof spec.parts !== "object") errors.push("parts is required");
  if (!spec?.features || typeof spec.features !== "object") errors.push("features is required");
  if (!Array.isArray(spec?.materials)) errors.push("materials must be an array");
  requireString(spec?.titleBlock, "sheetName", errors, "titleBlock");

  return {
    ok: errors.length === 0,
    errors,
  };
}

function requireString(obj, key, errors, scope = "") {
  if (!obj || typeof obj[key] !== "string" || obj[key].trim() === "") {
    errors.push(`${scope ? `${scope}.` : ""}${key} must be a non-empty string`);
  }
}

function requireNumber(obj, key, errors, scope = "") {
  if (!obj || typeof obj[key] !== "number" || !Number.isFinite(obj[key]) || obj[key] <= 0) {
    errors.push(`${scope ? `${scope}.` : ""}${key} must be a positive number`);
  }
}
