import { PageHeader } from "@/components/page-header";
import { SettingsForm } from "@/components/settings-form";

export default function SettingsPage() { return <div className="page-stack"><PageHeader eyebrow="CONTROL CENTRE SETTINGS" title="Your dashboard preferences" description="These settings affect this browser only. They never change your FPL team." /><section className="surface settings-intro"><strong>Recommended setup</strong><p>Use Malaysia (MYT), keep Decision Assistant as your landing page, enable deadline reminders, and use the Season Journal for week-by-week review after each Gameweek locks.</p></section><SettingsForm /></div>; }
