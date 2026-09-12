/* Match the server's global → profile → conversation → message parameter layers. */
const mapping = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

export function mergeSelectionParameters(...layers) {
  let merged = {};
  for (const layer of layers) {
    if (!mapping(layer)) continue;
    for (const [key, value] of Object.entries(layer)) {
      const previous = Object.hasOwn(merged, key) ? merged[key] : undefined;
      merged = { ...merged, [key]: mapping(value)
        ? mergeSelectionParameters(previous, value) : value };
    }
  }
  return merged;
}
