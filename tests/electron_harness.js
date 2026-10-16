// The desktop app (shell/main.js) for the tests (test_desktop_modules.py): its own userData (so it runs next
// to an installed JARVIS, which holds the single-instance lock of the real one), and nothing registered in
// the OS (jarvis:// links, the app's identity). Started as: electron tests/electron_harness.js --demarrage
"use strict";
const { app } = require("electron");
const path = require("path");

app.setPath("userData", process.env.JARVIS_TEST_USERDATA);
app.setAsDefaultProtocolClient = () => true;
app.setAppUserModelId = () => {};
require(path.join(__dirname, "..", "shell", "main.js"));
