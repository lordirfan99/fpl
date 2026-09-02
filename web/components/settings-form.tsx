"use client";

import { useEffect, useState } from "react";

type Settings = { league: string; timezone: string; landing: string; reminders: boolean; compact: boolean };
const defaults: Settings = { league: "58005", timezone: "Asia/Kuala_Lumpur", landing: "/assistant", reminders: true, compact: false };

export function SettingsForm() {
  const [settings, setSettings] = useState<Settings>(defaults); const [saved, setSaved] = useState(false);
  useEffect(() => { try { const value = window.localStorage.getItem("fpl-scout-settings-v1"); if (value) setSettings({ ...defaults, ...JSON.parse(value) }); } catch { /* use defaults */ } }, []);
  function update<K extends keyof Settings>(key: K, value: Settings[K]) { setSettings((current) => ({ ...current, [key]: value })); setSaved(false); }
  function save() { window.localStorage.setItem("fpl-scout-settings-v1", JSON.stringify(settings)); setSaved(true); }
  function reset() { setSettings(defaults); window.localStorage.removeItem("fpl-scout-settings-v1"); setSaved(true); }
  return <section className="surface settings-form"><div className="settings-group"><label>Default league<input inputMode="numeric" value={settings.league} onChange={(event) => update("league", event.target.value.replace(/\D/g, ""))} /><small>Used for League, Elite, Compare and Analytics links.</small></label><label>Timezone<select value={settings.timezone} onChange={(event) => update("timezone", event.target.value)}><option value="Asia/Kuala_Lumpur">Malaysia (MYT)</option><option value="Europe/London">United Kingdom</option><option value="UTC">UTC</option></select><small>Deadlines and timestamps are shown in this timezone.</small></label><label>Open dashboard on<select value={settings.landing} onChange={(event) => update("landing", event.target.value)}><option value="/assistant">Decision Assistant</option><option value="/my-team">My Team</option><option value="/journal">Season Journal</option><option value="/planner">Planner</option></select><small>Recommended: Assistant, because it answers what to do before the next deadline.</small></label></div><div className="settings-group settings-toggles"><label><input type="checkbox" checked={settings.reminders} onChange={(event) => update("reminders", event.target.checked)} /><span><strong>Deadline reminders</strong><small>Show deadline warnings in the dashboard.</small></span></label><label><input type="checkbox" checked={settings.compact} onChange={(event) => update("compact", event.target.checked)} /><span><strong>Compact research tables</strong><small>Reduce row spacing on dense League and Player views.</small></span></label></div><div className="settings-actions"><button type="button" onClick={save}>{saved ? "Saved" : "Save settings"}</button><button type="button" className="quiet" onClick={reset}>Reset defaults</button></div></section>;
}
