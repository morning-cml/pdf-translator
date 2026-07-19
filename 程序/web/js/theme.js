/* L4 皮肤加载器：换肤 = 换一个 <link> 的 href。记忆在 localStorage。 */
import { api } from "./api.js";

const LINK = document.getElementById("theme-css");
const KEY = "pdf-trans-theme";

export async function initThemes() {
  let themes = await api.themes().catch(() => []);
  if (!Array.isArray(themes) || !themes.length)
    themes = [{ id: "clean", name: "默认", emoji: "⬜" }];
  const wrap = document.getElementById("theme-switch");
  const saved = localStorage.getItem(KEY) || "sponge";

  function apply(id) {
    LINK.href = `/web/themes/${id}/theme.css`;
    document.documentElement.dataset.theme = id;
    localStorage.setItem(KEY, id);
    wrap.querySelectorAll("button").forEach(
      (b) => b.classList.toggle("active", b.dataset.id === id));
  }

  wrap.innerHTML = "";
  for (const t of themes) {
    const b = document.createElement("button");
    b.textContent = t.emoji || "🎨";
    b.title = t.name;
    b.dataset.id = t.id;
    b.addEventListener("click", () => apply(t.id));
    wrap.appendChild(b);
  }
  apply(themes.some((t) => t.id === saved) ? saved : (themes[0]?.id || "clean"));
}
