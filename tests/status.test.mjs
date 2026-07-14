import test from "node:test";
import assert from "node:assert/strict";

import { getStatusValues } from "../public/status.js";

test("unit mismatch replaces the normal status badge", () => {
  assert.deepEqual(
    getStatusValues({ status: "price_only", status_flags: ["unit_mismatch"] }),
    ["unit_mismatch"]
  );
});

test("ordinary statuses remain unchanged", () => {
  assert.deepEqual(getStatusValues({ status: "linked" }), ["linked"]);
});
