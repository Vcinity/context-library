export type Envelope<T> = {
  status: "ok" | "pending" | "error";
  data: T;
  errors: Array<{ code: string; message: string }>;
};

export async function csrf(path: string, method = "POST"): Promise<string> {
  const query = new URLSearchParams({ method, path });
  const response = await fetch(`/api/v1/session/csrf?${query}`, {
    credentials: "same-origin"
  });
  const payload = (await response.json()) as Envelope<{ csrf_token: string }>;
  if (!response.ok) throw new Error(payload.errors[0]?.message ?? "CSRF request failed");
  return payload.data.csrf_token;
}

export async function request<T>(
  path: string,
  init: RequestInit = {}
): Promise<Envelope<T>> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  if (init.method && init.method !== "GET") {
    headers.set("X-CSRF-Token", await csrf(path, init.method));
  }
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin"
  });
  const payload = (await response.json()) as Envelope<T>;
  if (!response.ok) throw new Error(payload.errors[0]?.message ?? "Request failed");
  return payload;
}
