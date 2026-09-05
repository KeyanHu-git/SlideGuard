import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { getCurrentWindow } from "@tauri-apps/api/window";
import {
  File,
  FolderOpen,
  Link2,
  Unlink,
  Minus,
  Square,
  X,
  ArrowUpFromLine,
  Check,
  ChevronDown,
  LoaderCircle,
  Image,
  FileCheck2,
} from "lucide-react";
import {
  assetUrl,
  call,
  chooseInput,
  chooseOutput,
  windowAction,
} from "./bridge";
import { emptyDocument, retainNewest, type DocumentState } from "./model";
import { Viewport } from "./Viewport";
import "./style.css";

function App() {
  const [doc, setDoc] = useState(emptyDocument),
    [error, setError] = useState(""),
    [linked, setLinked] = useState(true);
  const [sourceUrl, setSourceUrl] = useState(""),
    [resultUrl, setResultUrl] = useState(""),
    [resultMode, setResultMode] = useState(false);
  const [exact, setExact] = useState(false),
    [pending, setPending] = useState(0);
  const mounted = useRef(true);
  const apply = (value: DocumentState) => {
    if (mounted.current) setDoc((old) => retainNewest(old, value));
  };
  const perform = async (fn: () => Promise<DocumentState>) => {
    setError("");
    setPending((n) => n + 1);
    try {
      apply(await fn());
    } catch (e) {
      setError(String(e));
      throw e;
    } finally {
      setPending((n) => n - 1);
    }
  };
  const command = async (method: string, params = {}) => {
    await perform(() => call(method, params));
  };
  const edit = async (action: string, more = {}) => {
    try {
      await command("edit", { action, ...more });
    } catch {
      /* error already visible */
    }
  };
  useEffect(() => {
    mounted.current = true;
    let stopped = false;
    // Explicit method keeps the protocol small and inspectable.
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        apply(await call("state"));
      } catch (e) {
        if (!stopped) setError(String(e));
      }
      if (!stopped) timer = setTimeout(poll, 900);
    };
    void poll();
    return () => {
      mounted.current = false;
      stopped = true;
      clearTimeout(timer);
    };
  }, []);
  useEffect(() => {
    let disposed = false,
      created = "";
    setSourceUrl("");
    setResultMode(false);
    if (doc.sourceAsset)
      void assetUrl(doc.sourceAsset, 4000)
        .then((url) => {
          created = url;
          if (disposed) URL.revokeObjectURL(url);
          else setSourceUrl(url);
        })
        .catch((e) => setError(String(e)));
    return () => {
      disposed = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [doc.sourceAsset]);
  const png = doc.results.find((x) => x.kind === "png" && x.asset);
  useEffect(() => {
    let disposed = false,
      created = "";
    setResultUrl("");
    if (png)
      void assetUrl(png.asset, 4000)
        .then((url) => {
          created = url;
          if (disposed) URL.revokeObjectURL(url);
          else setResultUrl(url);
        })
        .catch((e) => setError(String(e)));
    return () => {
      disposed = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [png?.asset]);
  const disabled = !!doc.busy || !doc.ready || resultMode;
  const cropMode =
    doc.mode === "auto"
      ? "auto"
      : doc.base.every((v, i) => Math.abs(v - (i < 2 ? 0 : 1)) < 1e-9)
        ? "full"
        : "manual";
  const input = () => {
    void perform(chooseInput).catch(() => {});
  };
  const close = () => {
    if (doc.busy) {
      setError("任务还在运行，请先取消或等待完成");
      return;
    }
    void windowAction("close").catch((e) => setError(String(e)));
  };
  return (
    <main className="app-shell">
      <header
        className="titlebar"
        onPointerDown={(e) => {
          if (
            e.button === 0 &&
            (e.target as HTMLElement).closest("[data-drag]")
          )
            void getCurrentWindow().startDragging();
        }}
        onDoubleClick={(e) => {
          if ((e.target as HTMLElement).closest("[data-drag]"))
            void windowAction("maximize");
        }}
      >
        <div className="brand" data-drag>
          SlideGuard <span data-drag>STUDIO</span>
        </div>
        <div className="document-title" data-drag>
          <File size={14} />
          <span data-drag>{doc.filename || "未打开文档"}</span>
        </div>
        <div className="window-controls">
          <button
            title="最小化"
            aria-label="最小化"
            onClick={() => void windowAction("minimize")}
          >
            <Minus size={15} />
          </button>
          <button
            title="最大化或还原"
            aria-label="最大化或还原"
            onClick={() => void windowAction("maximize")}
          >
            <Square size={12} />
          </button>
          <button
            title="关闭"
            aria-label="关闭"
            className="window-close"
            onClick={close}
          >
            <X size={16} />
          </button>
        </div>
      </header>
      <div className="workspace">
        <aside className="pages">
          <div className="rail-heading">
            <span>页面</span>
            <span>{doc.pages || ""}</span>
          </div>
          <button className="open-file" onClick={input} disabled={!!doc.busy}>
            <FolderOpen size={15} />
            打开 PPTX
          </button>
          <div className="page-list">
            {Array.from({ length: doc.pages }, (_, i) => (
              <button
                key={i}
                className={"page-card " + (doc.page === i + 1 ? "current" : "")}
                disabled={!!doc.busy}
                onClick={() =>
                  void command("page", { page: i + 1 }).catch(() => {})
                }
              >
                <div className="thumbnail">
                  {doc.page === i + 1 && sourceUrl ? (
                    <img src={sourceUrl} alt={"第" + (i + 1) + "页"} />
                  ) : (
                    <File size={22} />
                  )}
                </div>
                <span>{String(i + 1).padStart(2, "0")}</span>
              </button>
            ))}
          </div>
          <div className="rail-foot">
            <span className="local-dot" />
            本地处理
            <br />
            <small>不会上传原稿</small>
          </div>
        </aside>
        <div className="center">
          <Viewport
            doc={
              resultMode && png
                ? {
                    ...doc,
                    width: png.width || doc.width,
                    height: png.height || doc.height,
                  }
                : doc
            }
            url={resultMode ? resultUrl : sourceUrl}
            resultMode={resultMode}
            edit={edit}
          />
          <nav className="view-tabs" aria-label="预览类型">
            <button
              className={!resultMode ? "active" : ""}
              onClick={() => setResultMode(false)}
            >
              <File size={14} />
              源图
            </button>
            <button
              className={resultMode ? "active" : ""}
              disabled={!resultUrl}
              onClick={() => setResultMode(true)}
            >
              <Image size={14} />
              透明结果
            </button>
            <span>
              {resultMode && !doc.resultCurrent
                ? "参数已改变 · 当前显示上次导出"
                : "预览不会改变原稿"}
            </span>
          </nav>
        </div>
        <aside className="inspector">
          <div className="inspector-heading">
            导出设置 <span>{doc.ready ? "第 " + doc.page + " 页" : ""}</span>
          </div>
          <div className="properties">
            <section>
              <h3>
                裁剪范围 <span>{doc.mode === "auto" ? "自动" : "手动"}</span>
              </h3>
              <div className="segmented">
                {[
                  ["auto", "自动紧边"],
                  ["full", "整页"],
                  ["manual", "手动"],
                ].map(([v, t]) => (
                  <button
                    key={v}
                    disabled={disabled}
                    className={cropMode === v ? "selected" : ""}
                    onClick={() => void edit("mode", { value: v })}
                  >
                    {t}
                  </button>
                ))}
              </div>
              <p className="hint">
                拖动边框调整选区，Esc 取消。
                <br />
                按住空格可平移画布。
              </p>
              <button className="text-button" onClick={() => setExact(!exact)}>
                <ChevronDown size={13} className={exact ? "rotate" : ""} />
                精确坐标
              </button>
              {exact && (
                <div className="coordinates">
                  {["左", "上", "右", "下"].map((label, i) => (
                    <label key={i}>
                      {label}
                      <input
                        key={doc.base[i]}
                        aria-label={label + "边界百分比"}
                        type="number"
                        min="0"
                        max="100"
                        step=".1"
                        defaultValue={(doc.base[i] * 100).toFixed(2)}
                        disabled={disabled}
                        onBlur={(e) => {
                          const b = doc.base.map((v) => v * 100);
                          b[i] = Number(e.target.value);
                          void edit("bounds", { value: b });
                        }}
                      />
                      <span>%</span>
                    </label>
                  ))}
                </div>
              )}
            </section>
            <section>
              <h3>
                边缘留白
                <button
                  className={"link-button " + (linked ? "selected" : "")}
                  disabled={disabled}
                  onClick={() => {
                    setLinked(!linked);
                    if (!linked)
                      void edit("margin", { value: doc.margins[0], edge: -1 });
                  }}
                  title="四边联动或独立调整"
                >
                  {linked ? <Link2 size={14} /> : <Unlink size={14} />}{" "}
                  {linked ? "联动" : "独立"}
                </button>
              </h3>
              <div className="presets">
                {[0, 1, 2, 5].map((v) => (
                  <button
                    key={v}
                    disabled={disabled}
                    className={
                      doc.margins.every((x) => Math.abs(x - v) < 0.01)
                        ? "selected"
                        : ""
                    }
                    onClick={() => void edit("margin", { value: v, edge: -1 })}
                  >
                    {v}%
                  </button>
                ))}
              </div>
              {(linked ? [0] : [0, 1, 2, 3]).map((i) => (
                <label className="margin-slider" key={i}>
                  <span>{linked ? "四边" : ["左", "上", "右", "下"][i]}</span>
                  <input
                    aria-label={
                      linked
                        ? "四边留白"
                        : ["左留白", "上留白", "右留白", "下留白"][i]
                    }
                    type="range"
                    min="0"
                    max="20"
                    step=".1"
                    disabled={disabled}
                    value={doc.margins[i]}
                    onPointerDown={() => void edit("begin")}
                    onPointerUp={() => void edit("end")}
                    onChange={(e) =>
                      void edit("margin", {
                        value: Number(e.target.value),
                        edge: linked ? -1 : i,
                      })
                    }
                  />
                  <output>{doc.margins[i].toFixed(1)}%</output>
                </label>
              ))}
              <p className="hint">按选区宽高扩展，不超出幻灯片。</p>
              <div className="dimensions">
                <span>输出范围</span>
                <strong>
                  {doc.cropSize[0]} × {doc.cropSize[1]}
                </strong>
                <span>px</span>
              </div>
            </section>
            <section>
              <h3>输出文件</h3>
              <label className="field-row">
                紧凑版上限
                <select
                  aria-label="紧凑版大小上限"
                  value={doc.limit}
                  disabled={disabled}
                  onChange={(e) =>
                    void edit("budget", { value: Number(e.target.value) })
                  }
                >
                  {[1, 2.5, 5, 10].map((v) => (
                    <option key={v} value={v}>
                      {v} MB
                    </option>
                  ))}
                </select>
              </label>
              <p className="hint">
                完整 SVG 另行保留。紧凑 PDF / SVG
                可能压缩位图，不会把位图变为矢量。
              </p>
              <button
                className="path-button"
                title={doc.output}
                disabled={!!doc.busy}
                onClick={() => void perform(chooseOutput).catch(() => {})}
              >
                <FolderOpen size={15} />
                <span>保存位置</span>
                <ChevronDown size={13} />
              </button>
              <p className="path" title={doc.output}>
                {doc.output || "默认本机 SlideGuard 文件夹"}
              </p>
            </section>
            {doc.results.length > 0 && (
              <section>
                <h3>
                  上次导出{" "}
                  <span className={doc.verdict === "PASS" ? "pass" : ""}>
                    {doc.verdict}
                  </span>
                </h3>
                {doc.results.map((r, i) => (
                  <div className="result-row" key={i} title={r.name}>
                    <span>{r.kind.toUpperCase()}</span>
                    <span>{(r.bytes / 1e6).toFixed(2)} MB</span>
                  </div>
                ))}
                <button
                  className="path-button"
                  disabled={!!doc.busy}
                  onClick={() => void command("verify").catch(() => {})}
                >
                  <FileCheck2 size={15} />
                  复核文件完整性
                </button>
              </section>
            )}
          </div>
          <div className="export-dock">
            <div className="check-status">
              <span className={doc.check.includes("通过") ? "pass" : ""}>
                {doc.check}
              </span>
              {pending > 0 && <LoaderCircle size={12} className="spin" />}
            </div>
            <div className="export-actions">
              <button
                disabled={!doc.ready || !!doc.busy}
                onClick={() => void command("check").catch(() => {})}
              >
                <Check size={15} />
                检查
              </button>
              <button
                className="primary"
                disabled={!doc.ready || (!!doc.busy && doc.busy !== "export")}
                onClick={() =>
                  void command(
                    doc.busy === "export" ? "cancel" : "export",
                  ).catch(() => {})
                }
              >
                <ArrowUpFromLine size={15} />
                {doc.busy === "export" ? "取消导出" : "导出并验收"}
              </button>
            </div>
          </div>
        </aside>
      </div>
      <footer className={error ? "status-bar error" : "status-bar"}>
        <span>
          {doc.busy && <LoaderCircle size={12} className="spin" />}
          {error || doc.status || "准备就绪"}
        </span>
        <span>{doc.busy ? doc.elapsed + " 秒" : "Tauri · 开发预览"}</span>
      </footer>
    </main>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
