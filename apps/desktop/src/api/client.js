const DEFAULT_BACKEND_URL = import.meta.env?.VITE_BACKEND_URL || "http://127.0.0.1:8765";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export class ApiClient {
  constructor(baseUrl = DEFAULT_BACKEND_URL) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  async get(path, options) {
    return this.request("GET", path, undefined, options);
  }

  async post(path, payload = {}) {
    return this.request("POST", path, payload);
  }

  async patch(path, payload = {}) {
    return this.request("PATCH", path, payload);
  }

  async delete(path, payload = {}) {
    return this.request("DELETE", path, payload);
  }

  async upload(path, formData) {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method: "POST",
      body: formData,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new ApiError(data.error || `POST ${path} failed: ${response.status}`, response.status);
    }
    return data;
  }

  async request(method, path, payload, { signal, timeoutMs = 15000 } = {}) {
    const options = { method, headers: {}, signal };
    if (payload !== undefined && method !== "GET") {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(payload);
    }
    const controller = new AbortController();
    const abort = () => controller.abort();
    let timer;
    if (method === "GET") {
      options.signal = controller.signal;
      if (signal?.aborted) controller.abort();
      else signal?.addEventListener("abort", abort, { once: true });
      timer = setTimeout(abort, timeoutMs);
    }
    try {
      const response = await fetch(`${this.baseUrl}${path}`, options);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new ApiError(data.error || `${method} ${path} failed: ${response.status}`, response.status);
      }
      return data;
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
    }
  }
}

export { DEFAULT_BACKEND_URL };
