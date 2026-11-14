import { useEffect, useState } from "react";
import { useLiveData } from "./useLiveData";
import type { Status } from "./types";
import StrataView from "./components/StrataView";
import DiagnosisCard from "./components/DiagnosisCard";
import LiveChart from "./components/LiveChart";
import FaultPanel from "./components/FaultPanel";
import InfoFooter from "./components/InfoFooter";
import SettingsDialog from "./components/SettingsDialog";

export default function App() {
  const { latest, history, events, connected } = useLiveData();
  const [status, setStatus] = useState<Status | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

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
          <button className="header-btn" onClick={() => setSettingsOpen(true)}>
            Settings
          </button>
        </div>
      </header>

      <main className="grid">
        <StrataView layers={status?.layers ?? []} tick={latest} />
        <DiagnosisCard tick={latest} />
        <LiveChart history={history} events={events} />
        <FaultPanel injectable={status?.injectable ?? []} active={latest?.fault.active ?? null} />
      </main>

      <InfoFooter />
      {settingsOpen && <SettingsDialog onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}
