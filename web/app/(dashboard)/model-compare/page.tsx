import { redirect } from "next/navigation";

// Model XIs merged into the single Models page (/v5-lab).
export default function RetiredModelComparePage() {
  redirect("/v5-lab");
}
