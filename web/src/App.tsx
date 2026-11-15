import { useEffect, useState } from "react";
import { useLiveData } from "./useLiveData";
import type { Status } from "./types";
import StrataView from "./components/StrataView";
import DiagnosisCard from "./components/DiagnosisCard";
import LiveChart from "./components/LiveChart";
import FaultPanel from "./components/FaultPanel";
import InfoFooter from "./components/InfoFooter";
import SettingsDialog from "./components/SettingsDialog";
import WhyCard from "./components/WhyCard";
import HistoryCard from "./components/HistoryCard";
import RegionCard from "./components/RegionCard";
import { useTheme } from "./theme";

export default function App() {
  const { latest, history, events, connected } = useLiveData();
  const [status, setStatus] = useState<Status | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [theme, setTheme] = useTheme();
  const [historyAgent, setHistoryAgent] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/status")
      .then((r) => r.json())
      .then(setStatus)
      .catch(() => setStatus(null));
  }, [connected]);

  return (
    <div className="page">
      <header className="header">
        <h1 className="title">Strata</h1>
        <div className="badges">
          {status?.sim_mode && <span className="badge">Simulation</span>}
          <span className={`badge ${connected ? "" : "badge-muted"}`}>
            {connected ? "Live" : "Connecting…"}
          </span>
          <button
            className="header-btn"
            aria-pressed={theme === "contrast"}
            onClick={() => setTheme(theme === "contrast" ? "light" : "contrast")}
          >
            {theme === "contrast" ? "Standard colors" : "High contrast"}
          </button>
          <button className="header-btn" onClick={() => setSettingsOpen(true)}>
            Settings
          </button>
        </div>
      </header>

      <main className="grid">
        <StrataView layers={status?.layers ?? []} tick={latest} />
        <DiagnosisCard tick={latest} />
        <LiveChart history={history} events={events} theme={theme} />
        <FaultPanel injectable={status?.injectable ?? []} active={latest?.fault.active ?? null} />
        <WhyCard tick={latest} />
        <HistoryCard agentId={historyAgent} onAgentChange={setHistoryAgent} />
        <RegionCard selected={historyAgent} onSelect={setHistoryAgent} />
      </main>

      <InfoFooter />
      {settingsOpen && <SettingsDialog onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}
