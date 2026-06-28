import { describe, expect, it } from "vitest";
import { isTaskStatus, TASK_STATUSES } from "./index.js";

describe("isTaskStatus", () => {
  it("accepts every known status", () => {
    for (const status of TASK_STATUSES) {
      expect(isTaskStatus(status)).toBe(true);
    }
  });

  it("rejects unknown values", () => {
    expect(isTaskStatus("nope")).toBe(false);
    expect(isTaskStatus(42)).toBe(false);
    expect(isTaskStatus(undefined)).toBe(false);
  });
});
