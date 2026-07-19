/* L2 状态层：极简发布-订阅。组件之间只通过这里通信，不互相调用。 */
const state = { files: [], jobId: null, running: false };
const subs = {};

export const store = {
  get: (k) => state[k],
  set(k, v) {
    state[k] = v;
    (subs[k] || []).forEach((f) => f(v));
  },
  on(k, f) {
    (subs[k] = subs[k] || []).push(f);
  },
};
