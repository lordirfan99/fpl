import Link from "next/link";
import { signIn } from "@/auth";
export const dynamic = "force-dynamic";

export default function SignInPage() {
  const configured = !!(process.env.AUTH_SECRET && process.env.AUTH_GOOGLE_ID && process.env.AUTH_GOOGLE_SECRET && process.env.FPL_OWNER_EMAIL);
  return <main className="page-stack"><section className="surface"><h1>Your private decision room</h1>
    <p>Only the owner’s verified Google account can view the current squad, budget and personal plan. Your FPL password is never requested here.</p>
    {configured ? <form action={async () => { "use server"; await signIn("google", { redirectTo: "/this-week" }); }}><button type="submit">Sign in with Google</button></form>
      : <p role="status">Private sign-in is not configured yet. Public league research is still available.</p>}
    <p><Link href="/league">Continue to public league research</Link></p></section></main>;
}
