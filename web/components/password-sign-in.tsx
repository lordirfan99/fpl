"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import { signIn } from "next-auth/react";

export function PasswordSignIn() {
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setPending(true);
    const form = new FormData(event.currentTarget);
    try {
      const result = await signIn("credentials", {
        password: form.get("password"),
        redirect: false,
        redirectTo: "/this-week",
      });
      if (!result.ok || result.error) {
        setError("Incorrect password.");
        setPending(false);
        return;
      }
      window.location.assign(result.url || "/this-week");
    } catch {
      setError("Sign-in is temporarily unavailable. Try again.");
      setPending(false);
    }
  }

  return <form className="password-sign-in" onSubmit={submit}>
    <label htmlFor="dashboard-password">Dashboard password</label>
    <input id="dashboard-password" name="password" type="password" autoComplete="current-password" required maxLength={256} />
    <button type="submit" disabled={pending}>{pending ? "Checking…" : "Unlock decision room"}</button>
    <p className="form-error" role="alert" aria-live="polite">{error}</p>
  </form>;
}
