import { useState } from "react";

/** Credits and the full icon license text, loaded from the bundled original file. */
export default function InfoFooter() {
  const [license, setLicense] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const toggle = async () => {
    if (!open && license == null) {
      try {
        const res = await fetch("/icons/fluent/LICENSE");
        setLicense(res.ok ? await res.text() : "License file not found.");
      } catch {
        setLicense("License file could not be loaded.");
      }
    }
    setOpen(!open);
  };

  return (
    <footer className="info">
      <p className="caption">
        Icons: Fluent Emoji (Flat) by Microsoft,{" "}
        <a href="https://github.com/microsoft/fluentui-emoji" target="_blank" rel="noreferrer">
          github.com/microsoft/fluentui-emoji
        </a>
        , MIT License, used unmodified. Font: Pretendard.{" "}
        <button className="link-btn" onClick={toggle} aria-expanded={open}>
          {open ? "Hide icon license" : "Show icon license"}
        </button>
      </p>
      {open && <pre className="license">{license ?? "Loading…"}</pre>}
    </footer>
  );
}
