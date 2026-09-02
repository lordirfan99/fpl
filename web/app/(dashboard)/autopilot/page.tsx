import { redirect } from "next/navigation";

// The GCP Autopilot bridge and its external execution channel were retired.
// Every FPL change is now made manually by the owner in the official app; the
// Assistant carries the read-only recommendation that used to live here.
export default function RetiredAutopilotPage() {
  redirect("/assistant");
}
