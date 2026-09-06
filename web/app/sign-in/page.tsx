import Link from "next/link";
import { PasswordSignIn } from "@/components/password-sign-in";
export const dynamic = "force-dynamic";

export default function SignInPage() {
  const configured = !!(process.env.AUTH_SECRET && process.env.FPL_DASHBOARD_PASSWORD);
  return <main className="page-stack"><section className="surface"><h1>Your private decision room</h1>
    <p>Enter the dashboard password to view the current squad, budget and the same personal plan as Telegram. Your FPL account password is never requested here.</p>
    {configured ? <PasswordSignIn />
      : <p role="status">Private sign-in is not configured yet. Public league research is still available.</p>}
    <p><Link href="/league">Continue to public league research</Link></p></section></main>;
}
