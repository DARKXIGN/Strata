import { useState } from "react";

/** Credits and the full Tossface license text, loaded from the bundled original file. */
export default function InfoFooter() {
  const [license, setLicense] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const toggle = async () => {
    if (!open && license == null) {
      try {
        const res = await fetch("/tossface/LICENSE");
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
        Icons: <span className="tossface">{"\u{1F4BB}"}</span> Tossface by Toss (Viva Republica),{" "}
        <a href="https://toss.im/tossface" target="_blank" rel="noreferrer">
          toss.im/tossface
        </a>
        , used unmodified. Copyright (c) 2022, 2023 Viva Republica, with Reserved Font Name Tossface. Font:
        Pretendard.{" "}
        <button className="link-btn" onClick={toggle} aria-expanded={open}>
          {open ? "Hide Tossface license" : "Show Tossface license"}
        </button>
      </p>
      {open && <pre className="license">{license ?? "Loading…"}</pre>}
    </footer>
  );
}
