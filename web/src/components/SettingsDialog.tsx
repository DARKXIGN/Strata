import { useEffect, useRef, useState } from "react";
import type { ExplainSettings } from "../types";

type Mode = ExplainSettings["mode"];

const MODES: { value: Mode; label: string; hint: string }[] = [
  { value: "template", label: "Template", hint: "Pre-written sentences. Works offline." },
  { value: "local", label: "Local LLM", hint: "Ollama or another OpenAI-compatible server you run." },
  { value: "api", label: "API", hint: "A hosted OpenAI-compatible API with your key." },
];

interface TestResult {
  ok: boolean;
  seconds?: number;
  summary?: string;
  error?: string;
}

export default function SettingsDialog({ onClose }: { onClose: () => void }) {
  const [saved, setSaved] = useState<ExplainSettings | null>(null);
  const [mode, setMode] = useState<Mode>("template");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [key, setKey] = useState(""); // only what the user types now; the saved key never comes back
  const [clearKey, setClearKey] = useState(false);
  const [busy, setBusy] = useState<"test" | "save" | null>(null);
  const [result, setResult] = useState<TestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const dialog = useRef<HTMLDivElement>(null);
  // The parent re-renders on every tick, so onClose changes identity each second.
  // Keep it in a ref so the load-once effect below never re-runs and wipes the form.
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    fetch("/api/settings/explain")
      .then((r) => r.json())
      .then((s: ExplainSettings) => {
        setSaved(s);
        setMode(s.mode);
        setBaseUrl(s.base_url);
        setModel(s.model);
      })
      .catch(() => setError("Could not load settings."));
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close.current();
    window.addEventListener("keydown", onKey);
    dialog.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const body = () => ({
    mode,
    base_url: mode === "template" ? baseUrl : baseUrl.trim(),
    model: model.trim(),
    api_key: clearKey ? "" : key ? key : null,
  });

  const send = async (path: string, method: string) => {
    const res = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body()),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Request failed");
    return data;
  };

  const test = async () => {
    setBusy("test");
    setResult(null);
    setError(null);
    try {
      setResult(await send("/api/settings/explain/test", "POST"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const save = async () => {
    setBusy("save");
    setError(null);
    try {
      await send("/api/settings/explain", "PUT");
      setKey("");
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const pickMode = (m: Mode) => {
    setMode(m);
    setResult(null);
    if (m === "local" && !baseUrl) setBaseUrl(saved?.default_local_url ?? "http://localhost:11434/v1");
  };

  const llm = mode !== "template";
  const keyNote = saved?.api_key_set && !clearKey ? `Saved key: ${saved.api_key_masked}` : "No key saved";

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="settings-title" tabIndex={-1} ref={dialog}>
        <h2 className="card-title" id="settings-title">
          Explanation settings
        </h2>
        <p className="caption">
          The classifier decides the cause. This only changes who writes the explanation. Any LLM failure or a reply
          slower than {saved?.timeout ?? 3} s falls back to the template.
        </p>

        <div className="segmented" role="radiogroup" aria-label="Explanation mode">
          {MODES.map((m) => (
            <button
              key={m.value}
              role="radio"
              aria-checked={mode === m.value}
              className={`segment ${mode === m.value ? "segment-on" : ""}`}
              onClick={() => pickMode(m.value)}
            >
              {m.label}
            </button>
          ))}
        </div>
        <p className="caption">{MODES.find((m) => m.value === mode)?.hint}</p>

        {llm && (
          <div className="fields">
            <label className="field">
              <span className="field-label">Base URL</span>
              <input
                className="input"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={mode === "local" ? "http://localhost:11434/v1" : "https://api.openai.com/v1"}
                spellCheck={false}
                autoComplete="off"
              />
            </label>
            <label className="field">
              <span className="field-label">Model name</span>
              <input
                className="input"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder={mode === "local" ? "llama3.2" : "gpt-4o-mini"}
                spellCheck={false}
                autoComplete="off"
              />
            </label>
            <label className="field">
              <span className="field-label">
                API key {mode === "local" && <span className="field-optional">(optional)</span>}
              </span>
              <input
                className="input"
                type="password"
                value={key}
                onChange={(e) => {
                  setKey(e.target.value);
                  setClearKey(false);
                }}
                placeholder={saved?.api_key_set && !clearKey ? "Leave empty to keep the saved key" : "Paste a key"}
                autoComplete="new-password"
                spellCheck={false}
              />
              <span className="caption field-note">
                {keyNote}
                {saved?.api_key_set && !clearKey && (
                  <>
                    {" · "}
                    <button className="link-btn" onClick={() => setClearKey(true)}>
                      Remove saved key
                    </button>
                  </>
                )}
                {clearKey && " (will be removed on save)"}
              </span>
              <span className="caption field-note">
                Stored only on the server in config.local.json. It is never sent back to this page.
              </span>
            </label>
          </div>
        )}

        {result && (
          <div className={`notice ${result.ok ? "notice-ok" : "notice-bad"}`}>
            {result.ok ? (
              <>
                <strong>Connected in {result.seconds} s.</strong> Sample: “{result.summary}”
              </>
            ) : (
              <>
                <strong>Test failed:</strong> {result.error}
              </>
            )}
          </div>
        )}
        {error && <div className="notice notice-bad">{error}</div>}

        <div className="dialog-actions">
          <button className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          {llm && (
            <button className="btn btn-soft" onClick={test} disabled={busy !== null}>
              {busy === "test" ? "Testing…" : "Test connection"}
            </button>
          )}
          <button className="btn btn-primary" onClick={save} disabled={busy !== null}>
            {busy === "save" ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
