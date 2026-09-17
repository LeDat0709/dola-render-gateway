// Công cụ NGƯỜI BÁN tạo key theo máy. Khoá bí mật KHÔNG nằm trong repo: ~/.dola-license/private.pem
//   node scripts/license-admin.cjs init                              → tạo cặp khoá (1 lần), in khoá công khai
//   node scripts/license-admin.cjs make <MÃ-MÁY> <số ngày> [ghi chú] → in key cho khách (ghi vào sổ issued.json)
//   node scripts/license-admin.cjs revoke <KEY|MÃ-MÁY> [lý do]       → khoá từ xa (ký lại license/revoked.json)
//   node scripts/license-admin.cjs unrevoke <KEY|MÃ-MÁY>             → gỡ khoá
//   node scripts/license-admin.cjs revoked                           → xem danh sách đang khoá
// Có giao diện: node scripts/license-admin-ui.cjs
// Sau revoke/unrevoke: commit + push license/revoked.json lên nhánh main của LeDat0709 (app đọc bản đó mỗi giờ).
// Mất private.pem = không tạo được key mới cho bản app đang phát hành → SAO LƯU nó.
const crypto = require("crypto");
const fs = require("fs");
const S = require("./license-store.cjs");

const P = S.storePaths();
const die = (msg) => { console.error(msg); process.exit(1); };
const PUSH_HINT = "Đẩy lên để có hiệu lực (app kiểm mỗi giờ):\n  git add license/revoked.json && git commit -m \"chore(license): cập nhật danh sách khoá\" && git push mine HEAD:main";

const [cmd, ...args] = process.argv.slice(2);
try {
  if (cmd === "init") {
    if (fs.existsSync(P.priv)) {
      console.error(`Đã có khoá ở ${P.priv} — không ghi đè (ghi đè = mọi key đã bán mất hiệu lực).`);
      console.log(fs.readFileSync(P.pub, "utf8"));
      process.exit(1);
    }
    const { privateKey, publicKey } = crypto.generateKeyPairSync("ed25519");
    fs.mkdirSync(P.dir, { recursive: true, mode: 0o700 });
    fs.writeFileSync(P.priv, privateKey.export({ type: "pkcs8", format: "pem" }), { mode: 0o600 });
    fs.writeFileSync(P.pub, publicKey.export({ type: "spki", format: "pem" }));
    console.log(`Đã tạo khoá bí mật: ${P.priv} (SAO LƯU file này, không đưa ai)\nKhoá công khai (dán vào desktop/license.cjs):\n`);
    console.log(fs.readFileSync(P.pub, "utf8"));
  } else if (cmd === "make") {
    const [machine, days, ...note] = args;
    if (!machine || !days) die("Dùng: make <MÃ-MÁY> <số ngày> [ghi chú]");
    const r = S.makeKey(P, { machine, days: Number(days), note: note.join(" ") });
    console.log(`Máy ${S.fmtMachine(r.machine)} · ${days} ngày · hết hạn ${new Date(r.expiresAt).toLocaleString("vi-VN")}\n\n${r.key}`);
  } else if (cmd === "revoke" || cmd === "unrevoke") {
    const t = S.parseTarget(args[0]);
    const list = S.loadRevocations(P);
    let next;
    if (cmd === "revoke") next = S.applyRevoke(list, t, args.slice(1).join(" "));
    else {
      const u = S.applyUnrevoke(list, t);
      if (!u.changed) die("Không có trong danh sách khoá.");
      next = u.list;
    }
    const saved = S.saveRevocations(P, next);
    console.log(`Đã ký ${P.revoked}: ${saved.keys.length} key, ${saved.machines.length} máy bị khoá.\n${PUSH_HINT}`);
  } else if (cmd === "revoked") {
    const list = S.loadRevocations(P);
    console.log(`Ký lúc: ${list.t ? new Date(list.t).toLocaleString("vi-VN") : "(chưa có)"}`);
    for (const k of list.keys) console.log(`  key  ${k.id}  — ${k.reason}`);
    for (const m of list.machines) console.log(`  máy  ${S.fmtMachine(m.m)}  — ${m.reason}`);
  } else {
    die("Lệnh: init | make <MÃ-MÁY> <số ngày> [ghi chú] | revoke <KEY|MÃ-MÁY> [lý do] | unrevoke <KEY|MÃ-MÁY> | revoked");
  }
} catch (e) {
  die(e.message || String(e));
}
