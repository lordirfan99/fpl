import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import { passwordMatches } from "@/lib/password-policy";

const OWNER_ID = "fpl-owner";
const OWNER_SESSION_EMAIL = "owner@fpl.local";

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [Credentials({
    name: "Dashboard password",
    credentials: { password: { label: "Password", type: "password" } },
    authorize(credentials) {
      if (!passwordMatches(credentials?.password, process.env.FPL_DASHBOARD_PASSWORD)) return null;
      return { id: OWNER_ID, name: "FPL owner", email: OWNER_SESSION_EMAIL };
    },
  })],
  session: { strategy: "jwt", maxAge: 24 * 60 * 60 },
  pages: { signIn: "/sign-in", error: "/sign-in" },
  callbacks: {
    jwt({ token, account, user }) {
      if (account) token.ownerVerified = account.provider === "credentials" && user?.id === OWNER_ID;
      return token;
    },
    session({ session, token }) {
      // A copied or hand-crafted token must carry both claims signed by AUTH_SECRET.
      session.user.email = token.ownerVerified === true && token.sub === OWNER_ID ? OWNER_SESSION_EMAIL : "";
      return session;
    },
  },
});

export async function isOwner() {
  if (!process.env.AUTH_SECRET || !process.env.FPL_DASHBOARD_PASSWORD) return false;
  const session = await auth();
  return session?.user?.email === OWNER_SESSION_EMAIL;
}
