import { describe, it, expect } from "vitest";
import { emptyDocument, fitView, zoomView, retainNewest } from "./model";
describe("viewport does not edit document geometry", () => {
  it("centers the effective crop", () => {
    const b: [number, number, number, number] = [0.1, 0.2, 0.8, 0.6],
      v = fitView(800, 600, 4000, 2250, b);
    expect((b[0] + b[2]) * 2000 * v.scale + v.x).toBeCloseTo(400);
    expect((b[1] + b[3]) * 1125 * v.scale + v.y).toBeCloseTo(300);
  });
  it("anchors wheel zoom to the cursor", () => {
    const v = { scale: 0.4, x: 30, y: -20 },
      n = zoomView(v, 2.1, 400, 300);
    expect((400 - v.x) / v.scale).toBeCloseTo((400 - n.x) / n.scale);
    expect((300 - v.y) / v.scale).toBeCloseTo((300 - n.y) / n.scale);
  });
  it("rejects stale state arriving after an edit", () => {
    const current = { ...emptyDocument, revision: 4 };
    expect(retainNewest(current, { ...emptyDocument, revision: 3 })).toBe(
      current,
    );
  });
});
