import { invoke } from "@tauri-apps/api/core";
import type { DocumentState } from "./model";
let tail: Promise<unknown> = Promise.resolve();
// Serialize UI commands so gesture begin/end, page changes and export cannot overtake edits.
function enqueue<T>(run: () => Promise<T>): Promise<T> {
  const pending = tail.then(run);
  tail = pending.catch(() => undefined);
  return pending;
}
export function call<T = DocumentState>(
  method: string,
  params: Record<string, unknown> = {},
): Promise<T> {
  return enqueue(() => invoke<T>("desktop_call", { method, params }));
}
export const chooseInput = () =>
  enqueue(() => invoke<DocumentState>("choose_input"));
export const chooseOutput = () =>
  enqueue(() => invoke<DocumentState>("choose_output"));
export const windowAction = (action: string) =>
  invoke("window_action", { action });
export interface Asset {
  mime: string;
  data: string;
  width?: number;
  height?: number;
}
export async function assetUrl(id: string, width = 2400) {
  const asset = await call<Asset>("asset", { id, width });
  const bytes = Uint8Array.from(atob(asset.data), (c) => c.charCodeAt(0));
  return URL.createObjectURL(new Blob([bytes], { type: asset.mime }));
}
