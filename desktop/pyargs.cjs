// Đường dẫn script .py cho spawnPy (main.js): python chạy với cwd = thư mục dữ liệu, nên tên script
// tương đối phải trỏ về thư mục mã (app-python trong bản đóng gói). Trước đây chỉ đổi args[0], nên
// ["-u", "import_seedance_export.py", ...] bị python tìm script trong thư mục dữ liệu:
//   can't open file 'C:\Users\...\Roaming\Dola Studio\import_seedance_export.py'
// Chạy `node desktop/pyargs.cjs` để tự kiểm tra.
const path = require("path");

function resolvePyArgs(args, appDir) {
  const i = args.findIndex((x) => typeof x === "string" && x.endsWith(".py") && !path.isAbsolute(x));
  return i < 0 ? args.slice() : args.map((x, k) => (k === i ? path.join(appDir, x) : x));
}

module.exports = { resolvePyArgs };

if (require.main === module) {
  const assert = require("node:assert/strict");
  const D = path.join(path.sep, "app");
  assert.deepEqual(resolvePyArgs(["import_cookies.py", "n", "f"], D), [path.join(D, "import_cookies.py"), "n", "f"]);
  assert.deepEqual(resolvePyArgs(["-u", "import_seedance_export.py", "/tmp/x.json"], D), ["-u", path.join(D, "import_seedance_export.py"), "/tmp/x.json"], "script sau -u cũng phải được trỏ về thư mục mã");
  assert.deepEqual(resolvePyArgs(["-m", "uvicorn", "server:app"], D), ["-m", "uvicorn", "server:app"]);
  const abs = path.join(D, "a.py");
  assert.deepEqual(resolvePyArgs([abs], D), [abs], "đường dẫn tuyệt đối giữ nguyên");
  console.log("pyargs: OK");
}
