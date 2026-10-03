// The installed JARVIS application (electron-builder, see docs/electron.md, "Installer l'application").
// It finds the JARVIS folder (the one with start.bat) and runs that folder's shell/main.js: updating
// JARVIS (git pull) updates the application too, without installing it again. The main.js packed with
// the application runs only when the folder's one needs a newer Electron than the installed one; a
// notification then asks to install the application again (build-app.bat).
"use strict";
const { app, dialog, Notification } = require("electron");
const fs = require("fs");
const path = require("path");

const remembered = () => path.join(app.getPath("userData"), "dossier.json");
const isRoot = (dir) => !!dir && fs.existsSync(path.join(dir, "console", "__main__.py")) && fs.existsSync(path.join(dir, "shell", "main.js"));

function savedRoot() {
  try { return JSON.parse(fs.readFileSync(remembered(), "utf8")).root || ""; } catch { return ""; }
}

/** JARVIS_ROOT, else the folder of the last start (written by main.js, and by build-app.bat). */
function findRoot() {
  for (const dir of [process.env.JARVIS_ROOT, savedRoot()]) if (isRoot(dir)) return path.resolve(dir);
  return "";
}

/** First start without a known folder: the user shows it. */
function askRoot() {
  for (;;) {
    const picked = dialog.showOpenDialogSync({ title: "Où se trouve le dossier de JARVIS ?", properties: ["openDirectory"],
      message: "Choisis le dossier de JARVIS : celui qui contient start.bat (ou start.command)." });
    if (!picked?.length) return "";
    if (isRoot(picked[0])) return path.resolve(picked[0]);
    const again = dialog.showMessageBoxSync({ type: "warning", title: "JARVIS", buttons: ["Choisir un autre dossier", "Quitter"],
      message: "Ce dossier n'est pas celui de JARVIS.", detail: `${picked[0]}\n\nIl doit contenir start.bat et les dossiers console et shell.` });
    if (again !== 0) return "";
  }
}

/** The major version of Electron the folder's shell is written for (shell/package.json). */
function wantedElectron(root) {
  try {
    const pkg = JSON.parse(fs.readFileSync(path.join(root, "shell", "package.json"), "utf8"));
    return Number(String(pkg.devDependencies?.electron || "").match(/\d+/)?.[0]) || 0;
  } catch { return 0; }
}

function run(root) {
  process.env.JARVIS_ROOT = root;
  try { fs.mkdirSync(path.dirname(remembered()), { recursive: true }); fs.writeFileSync(remembered(), JSON.stringify({ root }, null, 2)); }
  catch { /* asked again next time */ }
  const have = Number(process.versions.electron.split(".")[0]);
  const want = wantedElectron(root);
  if (want > have) {
    // the folder's shell needs a newer Electron: the packed one meanwhile, and a word to the user
    app.whenReady().then(() => {
      if (!Notification.isSupported()) return;
      new Notification({ title: "JARVIS : nouvelle version de l'application", body: process.platform === "win32"
        ? "Lance build-app.bat dans le dossier de JARVIS pour l'installer (les tâches continuent)."
        : "Lance build-app.command dans le dossier de JARVIS pour l'installer." }).show();
    });
    require("./main.js");
    return;
  }
  require(path.join(root, "shell", "main.js"));
}

const root = findRoot();
if (root) run(root);
else {
  app.whenReady().then(() => {
    const picked = askRoot();
    if (picked) run(picked);
    else app.quit();
  });
}
