/* 装配入口：初始化皮肤 → 各组件。 */
import { initThemes } from "./theme.js";
import { initFiles, initSettings, initRun, toast } from "./components.js";

(async () => {
  try {
    await initThemes();
    initFiles();
    await initSettings();
    initRun();
  } catch (e) {
    console.error(e);
    toast("界面初始化失败：" + e.message);
  }
})();
