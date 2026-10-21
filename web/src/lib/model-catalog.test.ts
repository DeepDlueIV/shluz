import assert from "node:assert/strict";
import test from "node:test";
import { modelRows, modelCatalogSource } from "./model-catalog.ts";

 test("Reference carousel includes every unique name once across its two rows", () => {
  const names = modelRows.flat();
  assert.equal(names.length, 20);
  assert.equal(new Set(names).size, names.length);
  assert.deepEqual(modelRows.map(row => row.length), [11, 9]);
  for (const name of ["OpenAI", "Claude", "Black Forest Labs", "NVIDIA", "Vidu", "PixVerse"]) assert.ok(names.some(item => item === name));
  assert.equal(modelCatalogSource, "https://venice.ai/models");
});
