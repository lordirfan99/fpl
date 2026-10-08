// Keep the timeout attached while reading the body, not just response headers.
// A streamed HTTP 200 can otherwise leave a page pending indefinitely.
export const API_TIMEOUT_MS = 8000;

export function fetchApi(url: string, init: RequestInit = {}, timeoutMs = API_TIMEOUT_MS) {
  const timeout = AbortSignal.timeout(timeoutMs);
  return fetch(url, { ...init, signal: init.signal ? AbortSignal.any([init.signal, timeout]) : timeout });
}
