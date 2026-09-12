// Thư mục dữ liệu của bản đóng gói (nick, cấu hình, DB, video tải về).
// KHÔNG dùng userData mặc định của Electron: tên gói "dola-desktop" trùng với một tool khác cũng viết
// bằng Electron → hai app ghi chung một thư mục, file trộn vào nhau (thấy trên máy Mac có cài tool đó).
// Bản 1.0.6 trở về trước đã ghi vào userData → dọn các mục CỦA TA sang một lần; file của tool kia
// (config.json, jobs.json, profiles/, logs/…) để yên. Cùng ổ nên rename là tức thì.
const path = require("path");

const DIR_NAME = "Dola Studio";
const OURS = [".env.local", "accounts", "downloads", "tasks.db", "pool_usage.db"];

function resolveDataDir({ appData, oldUserData, fs, log = () => {} }) {
  const dir = path.join(appData, DIR_NAME);
  fs.mkdirSync(dir, { recursive: true });
  if (!oldUserData || oldUserData === dir) return dir;
  for (const f of OURS) {
    const src = path.join(oldUserData, f);
    const dst = path.join(dir, f);
    if (!fs.existsSync(src) || fs.existsSync(dst)) continue;
    try {
      fs.renameSync(src, dst);
      log(`[data] dọn ${f} từ thư mục cũ sang ${dir}`);
    } catch (e) {
      log(`[data] không dọn được ${f}: ${e}`);
    }
  }
  return dir;
}

module.exports = { resolveDataDir, DIR_NAME, OURS };
