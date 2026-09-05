import { useEffect, useRef, useState } from "react";
import { Crop, Hand, Minus, Plus, Scan, Undo2, Redo2 } from "lucide-react";
import type { DocumentState, View } from "./model";
import { fitView, zoomView } from "./model";

type Edit = (action: string, more?: Record<string, unknown>) => Promise<void>;
export function Viewport({
  doc,
  url,
  resultMode,
  edit,
}: {
  doc: DocumentState;
  url: string;
  resultMode: boolean;
  edit: Edit;
}) {
  const canvas = useRef<HTMLDivElement>(null),
    viewRef = useRef<View>({ scale: 0.2, x: 0, y: 0 });
  const [view, setView] = useState(viewRef.current),
    [hand, setHand] = useState(false),
    [space, setSpace] = useState(false);
  const drag = useRef<{
    kind: string;
    x: number;
    y: number;
    view: View;
  } | null>(null);
  const [cursorPoint, setCursorPoint] = useState<{
    x: number;
    y: number;
  } | null>(null);
  const latestDrag = useRef<Record<string, unknown> | null>(null),
    dragPump = useRef<Promise<void> | null>(null);
  const flushDrag = (): Promise<void> => {
    if (dragPump.current) return dragPump.current;
    dragPump.current = (async () => {
      while (latestDrag.current) {
        const value = latestDrag.current;
        latestDrag.current = null;
        await edit("drag", value);
      }
    })().finally(() => {
      dragPump.current = null;
    });
    return dragPump.current;
  };
  const queueDrag = (p: { x: number; y: number }) => {
    const d = drag.current;
    if (!d || d.kind === "pan") return;
    const v = d.view;
    latestDrag.current = {
      handle: d.kind,
      dx: (p.x - d.x) / (doc.width * v.scale),
      dy: (p.y - d.y) / (doc.height * v.scale),
      x: (p.x - v.x) / (doc.width * v.scale),
      y: (p.y - v.y) / (doc.height * v.scale),
    };
    void flushDrag();
  };
  const setCamera = (next: View) => {
    viewRef.current = next;
    setView(next);
  };
  const fit = () => {
    const c = canvas.current;
    if (c)
      setCamera(
        fitView(
          c.clientWidth,
          c.clientHeight,
          doc.width,
          doc.height,
          resultMode ? [0, 0, 1, 1] : doc.effective,
        ),
      );
  };
  useEffect(() => {
    fit();
  }, [doc.sourceAsset, resultMode, url]);
  const fitRef = useRef(fit);
  fitRef.current = fit;
  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const observer = new ResizeObserver(() => fitRef.current());
    observer.observe(c);
    return () => observer.disconnect();
  }, [doc.sourceAsset, resultMode]);
  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const b = c.getBoundingClientRect();
      setCamera(
        zoomView(
          viewRef.current,
          Math.exp(-e.deltaY * 0.0015),
          e.clientX - b.left,
          e.clientY - b.top,
        ),
      );
    };
    c.addEventListener("wheel", wheel, { passive: false });
    return () => c.removeEventListener("wheel", wheel);
  }, []);
  const point = (e: React.PointerEvent) => {
    const b = canvas.current!.getBoundingClientRect();
    return { x: e.clientX - b.left, y: e.clientY - b.top };
  };
  const start = (e: React.PointerEvent, kind: string) => {
    if (!doc.ready) return;
    if (kind !== "pan" && (doc.busy || resultMode)) return;
    e.preventDefault();
    e.stopPropagation();
    canvas.current!.focus();
    e.currentTarget.setPointerCapture(e.pointerId);
    const p = point(e);
    drag.current = { kind, ...p, view: viewRef.current };
    if (kind !== "pan") void edit("begin");
  };
  const move = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const p = point(e);
    if (d.kind === "pan")
      setCamera({
        ...d.view,
        x: d.view.x + p.x - d.x,
        y: d.view.y + p.y - d.y,
      });
    else {
      setCursorPoint(p);
      queueDrag(p);
    }
  };
  const end = async (e: React.PointerEvent, cancel = false) => {
    const d = drag.current;
    if (!d) return;
    if (d.kind === "pan") {
      drag.current = null;
      return;
    }
    if (!cancel) queueDrag(point(e));
    else latestDrag.current = null;
    drag.current = null;
    setCursorPoint(null);
    try {
      if (dragPump.current) await dragPump.current;
    } finally {
      await edit("end", { cancel });
    }
  };
  const rectStyle = (b: number[]) => ({
    left: b[0] * 100 + "%",
    top: b[1] * 100 + "%",
    width: (b[2] - b[0]) * 100 + "%",
    height: (b[3] - b[1]) * 100 + "%",
  });
  return (
    <section className="workarea">
      <div className="canvas-tools">
        <div className="tool-group">
          <button
            title="裁剪"
            aria-label="裁剪工具"
            className={!hand ? "selected" : ""}
            onClick={() => setHand(false)}
          >
            <Crop size={16} />
          </button>
          <button
            title="平移 · 空格拖动"
            aria-label="平移工具"
            className={hand ? "selected" : ""}
            onClick={() => setHand(true)}
          >
            <Hand size={16} />
          </button>
          <span className="divider" />
          <button
            title="撤销"
            aria-label="撤销"
            disabled={!doc.canUndo || !!doc.busy || resultMode}
            onClick={() => void edit("undo")}
          >
            <Undo2 size={16} />
          </button>
          <button
            title="重做"
            aria-label="重做"
            disabled={!doc.canRedo || !!doc.busy || resultMode}
            onClick={() => void edit("redo")}
          >
            <Redo2 size={16} />
          </button>
        </div>
        <span className="view-label">
          {resultMode ? "上次导出 · 透明 PNG" : "源图 · 裁剪工作区"}
        </span>
        <div className="tool-group">
          <button
            aria-label="缩小"
            onClick={() =>
              setCamera(
                zoomView(
                  view,
                  0.8,
                  canvas.current!.clientWidth / 2,
                  canvas.current!.clientHeight / 2,
                ),
              )
            }
          >
            <Minus size={15} />
          </button>
          <span className="zoom-value">{Math.round(view.scale * 100)}%</span>
          <button
            aria-label="放大"
            onClick={() =>
              setCamera(
                zoomView(
                  view,
                  1.25,
                  canvas.current!.clientWidth / 2,
                  canvas.current!.clientHeight / 2,
                ),
              )
            }
          >
            <Plus size={15} />
          </button>
          <button title="适合选区" aria-label="适合选区" onClick={fit}>
            <Scan size={16} />
          </button>
          <button
            title="原生参考图实际像素"
            onClick={() =>
              setCamera(
                zoomView(
                  view,
                  1 / view.scale,
                  canvas.current!.clientWidth / 2,
                  canvas.current!.clientHeight / 2,
                ),
              )
            }
          >
            1:1
          </button>
        </div>
      </div>
      <div
        className={"viewport " + (hand || space ? "hand" : "")}
        ref={canvas}
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.code === "Space") {
            e.preventDefault();
            setSpace(true);
          }
          if (e.key === "Escape") {
            drag.current = null;
            latestDrag.current = null;
            setCursorPoint(null);
            void (async () => {
              if (dragPump.current) await dragPump.current;
              await edit("end", { cancel: true });
            })();
          }
          if (
            (e.ctrlKey || e.metaKey) &&
            e.key.toLowerCase() === "z" &&
            !drag.current &&
            !doc.busy &&
            !resultMode
          ) {
            e.preventDefault();
            void edit(e.shiftKey ? "redo" : "undo");
          }
        }}
        onKeyUp={(e) => {
          if (e.code === "Space") setSpace(false);
        }}
        onBlur={() => setSpace(false)}
        onPointerDown={(e) => {
          if (hand || space || e.button === 1) start(e, "pan");
        }}
        onPointerMove={move}
        onPointerUp={(e) => void end(e)}
        onPointerCancel={(e) => void end(e, true)}
        onContextMenu={(e) => e.preventDefault()}
      >
        {doc.ready && url ? (
          <div
            className={"slide " + (resultMode ? "checker" : "")}
            style={{
              left: view.x,
              top: view.y,
              width: doc.width * view.scale,
              height: doc.height * view.scale,
            }}
          >
            <img
              src={url}
              draggable={false}
              alt={resultMode ? "上次导出的透明PNG" : "当前幻灯片原生参考图"}
            />
            {!resultMode && (
              <>
                <div
                  className="output-outline"
                  style={rectStyle(doc.effective)}
                />
                <div
                  className="crop-outline"
                  style={rectStyle(doc.base)}
                  onPointerDown={(e) =>
                    start(e, hand || space ? "pan" : "move")
                  }
                >
                  {["nw", "n", "ne", "e", "se", "s", "sw", "w"].map((h) => (
                    <div
                      key={h}
                      className={"crop-handle " + h}
                      role="button"
                      aria-label={"裁剪手柄 " + h}
                      onPointerDown={(e) => start(e, hand || space ? "pan" : h)}
                    />
                  ))}
                </div>
              </>
            )}
          </div>
        ) : (
          <div className="empty-canvas">
            <Crop size={36} strokeWidth={1} />
            <h2>{doc.busy ? "正在读取幻灯片" : "从一张幻灯片开始"}</h2>
            <p>
              {doc.busy
                ? "正在生成原生参考图，稿件不会上传"
                : "打开 PPTX，直接调整裁剪范围和边缘留白"}
            </p>
          </div>
        )}
        {cursorPoint && (
          <div
            className="drag-guide"
            style={{ left: cursorPoint.x, top: cursorPoint.y }}
          >
            Esc 取消
          </div>
        )}
        <div className="canvas-note">
          {resultMode
            ? "棋盘格表示透明，不写入图片"
            : "蓝框：裁剪范围　绿框：输出范围"}
        </div>
      </div>
    </section>
  );
}
