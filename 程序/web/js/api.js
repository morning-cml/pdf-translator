/* L2 通信层：所有 HTTP 都从这里走。未来云端化只需改 BASE。 */
const BASE = "";

async function j(url, opts) {
  const r = await fetch(BASE + url, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok && !data.error) data.error = `HTTP ${r.status}`;
  return data;
}
const post = (url, body) => j(url, { method: "POST", body: JSON.stringify(body || {}) });

export const api = {
  config: () => j("/api/config"),
  saveConfig: (c) => post("/api/config", c),
  themes: () => j("/api/themes"),
  languages: () => j("/api/languages"),
  pick: (type = "pdf") => post("/api/pick", { type }),
  test: (o) => post("/api/test", o),
  start: (payload) => post("/api/jobs", payload),
  job: (id) => j("/api/jobs/" + id),
  cancel: (id) => post("/api/jobs/" + id + "/cancel"),
  open: (path, folder = false) => post("/api/open", { path, folder }),
};
