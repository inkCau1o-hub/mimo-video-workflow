const DEFAULT_BACKEND_URL = import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:8765";

export class ApiClient {
  constructor(baseUrl = DEFAULT_BACKEND_URL) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  async get(path) {
    return this.request("GET", path);
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
      throw new Error(data.error || `POST ${path} failed: ${response.status}`);
    }
    return data;
  }

  async request(method, path, payload) {
    const options = { method, headers: {} };
    if (payload !== undefined && method !== "GET") {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(payload);
    }
    const response = await fetch(`${this.baseUrl}${path}`, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `${method} ${path} failed: ${response.status}`);
    }
    return data;
  }
}

export { DEFAULT_BACKEND_URL };
