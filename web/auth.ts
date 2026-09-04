import NextAuth from "next-auth";
import Google from "next-auth/providers/google";
import { ownerEmail as matchesOwner, verifiedGoogleOwner } from "@/lib/owner-policy";

export function ownerEmail(email?: string | null) {
  return matchesOwner(email, process.env.FPL_OWNER_EMAIL);
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [Google],
  session: { strategy: "jwt", maxAge: 24 * 60 * 60 },
  pages: { signIn: "/sign-in", error: "/sign-in" },
  callbacks: {
    signIn({ account, profile }) {
      return verifiedGoogleOwner(account?.provider, profile?.email_verified, profile?.email, process.env.FPL_OWNER_EMAIL);
    },
    jwt({ token, account, profile }) {
      if (account) token.ownerVerified = verifiedGoogleOwner(account.provider, profile?.email_verified, profile?.email, process.env.FPL_OWNER_EMAIL);
      return token;
    },
    session({ session, token }) {
      // Fail closed if allowlist changed since sign-in.
      if (token.ownerVerified !== true || !ownerEmail(token.email)) session.user.email = "";
      return session;
    },
  },
});

export async function isOwner() {
  if (!process.env.AUTH_SECRET || !process.env.FPL_OWNER_EMAIL) return false;
  const session = await auth();
  return ownerEmail(session?.user?.email);
}
