export type Rect = [number, number, number, number];
export type View = { scale: number; x: number; y: number };
export interface DocumentState {
  revision: number;
  filename: string;
  page: number;
  pages: number;
  ready: boolean;
  busy: string;
  elapsed: number;
  status: string;
  check: string;
  output: string;
  sourceAsset: string;
  width: number;
  height: number;
  base: Rect;
  effective: Rect;
  cropSize: [number, number];
  mode: string;
  margins: number[];
  limit: number;
  canUndo: boolean;
  canRedo: boolean;
  results: {
    kind: string;
    name: string;
    bytes: number;
    asset: string;
    width?: number;
    height?: number;
  }[];
  verdict: string | null;
  resultCurrent: boolean;
}
export const emptyDocument: DocumentState = {
  revision: 0,
  filename: "",
  page: 1,
  pages: 0,
  ready: false,
  busy: "",
  elapsed: 0,
  status: "",
  check: "尚未检查",
  output: "",
  sourceAsset: "",
  width: 4000,
  height: 2250,
  base: [0, 0, 1, 1],
  effective: [0, 0, 1, 1],
  cropSize: [4000, 2250],
  mode: "auto",
  margins: [0, 0, 0, 0],
  limit: 2.5,
  canUndo: false,
  canRedo: false,
  results: [],
  verdict: null,
  resultCurrent: false,
};
export function fitView(
  cw: number,
  ch: number,
  w: number,
  h: number,
  b: Rect,
): View {
  const scale = Math.min(
    Math.max(1, cw - 96) / Math.max(1, (b[2] - b[0]) * w),
    Math.max(1, ch - 96) / Math.max(1, (b[3] - b[1]) * h),
  );
  return {
    scale,
    x: cw / 2 - ((b[0] + b[2]) * w * scale) / 2,
    y: ch / 2 - ((b[1] + b[3]) * h * scale) / 2,
  };
}
export function zoomView(v: View, factor: number, x: number, y: number): View {
  const scale = Math.min(8, Math.max(0.02, v.scale * factor)),
    ratio = scale / v.scale;
  return { scale, x: x - (x - v.x) * ratio, y: y - (y - v.y) * ratio };
}
export function retainNewest(current: DocumentState, next: DocumentState) {
  return next.revision >= current.revision ? next : current;
}
