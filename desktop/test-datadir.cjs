#!/usr/bin/env node
// Kiểm tra thư mục dữ liệu bản đóng gói + dọn dữ liệu cũ (datadir.cjs): node desktop/test-datadir.cjs
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { resolveDataDir, DIR_NAME } = require("./datadir.cjs");

const appData = fs.mkdtempSync(path.join(os.tmpdir(), "dd-"));
const old = path.join(appData, "dola-desktop");
fs.mkdirSync(path.join(old, "accounts", "n1"), { recursive: true });
fs.writeFileSync(path.join(old, ".env.local"), "DOLA_PORT=8000\n");
fs.writeFileSync(path.join(old, "tasks.db"), "x");
fs.writeFileSync(path.join(old, "jobs.json"), "{}");                       // của tool kia
fs.mkdirSync(path.join(old, "logs"), { recursive: true });
fs.writeFileSync(path.join(old, "logs", "fail-task-1.json"), "{}");        // của tool kia

const logs = [];
const dir = resolveDataDir({ appData, oldUserData: old, fs, log: (m) => logs.push(m) });
assert.equal(dir, path.join(appData, DIR_NAME));
assert.ok(fs.existsSync(path.join(dir, "accounts", "n1")), "accounts được dọn sang");
assert.equal(fs.readFileSync(path.join(dir, ".env.local"), "utf8"), "DOLA_PORT=8000\n");
assert.ok(fs.existsSync(path.join(dir, "tasks.db")));
assert.ok(!fs.existsSync(path.join(old, "accounts")), "thư mục cũ không còn accounts");
assert.ok(fs.existsSync(path.join(old, "jobs.json")), "file tool khác để yên");
assert.ok(fs.existsSync(path.join(old, "logs", "fail-task-1.json")), "logs của tool khác để yên");
assert.ok(!fs.existsSync(path.join(dir, "jobs.json")));
assert.equal(logs.length, 3, logs.join("\n"));

// Chạy lần 2 khi đích đã có dữ liệu → không đụng vào (không ghi đè, không trộn)
fs.mkdirSync(path.join(old, "accounts", "n2"), { recursive: true });
resolveDataDir({ appData, oldUserData: old, fs });
assert.ok(fs.existsSync(path.join(old, "accounts", "n2")), "đích đã có accounts → nguồn giữ nguyên");
assert.ok(!fs.existsSync(path.join(dir, "accounts", "n2")));

// Máy mới, chưa từng có thư mục cũ → chỉ tạo thư mục mới
const fresh = fs.mkdtempSync(path.join(os.tmpdir(), "dd-"));
const d2 = resolveDataDir({ appData: fresh, oldUserData: path.join(fresh, "dola-desktop"), fs });
assert.ok(fs.existsSync(d2) && fs.readdirSync(d2).length === 0);

fs.rmSync(appData, { recursive: true, force: true });
fs.rmSync(fresh, { recursive: true, force: true });
console.log("test-datadir: OK");
