import { createHash } from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import { access, readFile, writeFile } from "node:fs/promises";
import { get } from "node:http";
import { createServer } from "node:net";
import { dirname, join, resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath, pathToFileURL } from "node:url";

const webDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const stampName = ".noclip-launcher-state";
const host = "127.0.0.1";
let ownedChild;
let stopping = false;

export function assertNodeVersion(version = process.versions.node) {
  const major = Number(version.split(".")[0]);
  if (!Number.isInteger(major) || major < 24) {
    throw new Error(`Нужен Node.js 24 или новее. Сейчас установлен ${version}. Обновите Node.js и повторите запуск.`);
  }
}

export async function dependencyState(directory) {
  const [manifest, lock] = await Promise.all([
    readFile(join(directory, "package.json")),
    readFile(join(directory, "package-lock.json")),
  ]);
  const fingerprint = createHash("sha256")
    .update(manifest).update(lock)
    .update(`${process.version}/${process.platform}/${process.arch}`)
    .digest("hex");
  try {
    await access(join(directory, "node_modules", "next", "dist", "bin", "next"));
    const installed = await readFile(join(directory, "node_modules", stampName), "utf8");
    return { fingerprint, needsInstall: installed !== fingerprint };
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
    return { fingerprint, needsInstall: true };
  }
}

function startChild(command, args) {
  // Отдельная группа не получает Ctrl+C раньше запускатора: taskkill завершит всё её дерево.
  const child = spawn(command, args, {
    cwd: webDir,
    stdio: ["ignore", "pipe", "pipe"],
    detached: true,
    windowsHide: true,
  });
  ownedChild = child;
  child.stdout.pipe(process.stdout, { end: false });
  child.stderr.pipe(process.stderr, { end: false });
  const running = { child, result: null, done: null };
  running.done = new Promise((resolveDone) => {
    const finish = (result) => {
      if (running.result) return;
      running.result = result;
      resolveDone(result);
    };
    child.once("error", (error) => finish({ code: 1, error }));
    child.once("exit", (code, signal) => finish({ code, signal }));
  });
  return running;
}

function stopOwnedChild() {
  if (!ownedChild?.pid || ownedChild.exitCode !== null || ownedChild.signalCode !== null) return;
  spawnSync("taskkill.exe", ["/PID", String(ownedChild.pid), "/T", "/F"], {
    windowsHide: true,
    stdio: "ignore",
    timeout: 5000,
  });
  ownedChild = undefined;
}

async function availablePort() {
  for (let port = 3000; port <= 3010; port += 1) {
    const free = await new Promise((resolveFree, reject) => {
      const probe = createServer();
      probe.once("error", (error) => {
        if (error.code === "EADDRINUSE" || error.code === "EACCES") resolveFree(false);
        else reject(error);
      });
      probe.listen({ host, port, exclusive: true }, () => probe.close(() => resolveFree(true)));
    });
    if (free) return port;
  }
  throw new Error("Порты 3000–3010 заняты или недоступны. Остановите предыдущий запуск сайта и попробуйте снова.");
}

function responds(url) {
  return new Promise((resolveReady) => {
    const request = get(url, { timeout: 3000 }, (response) => {
      response.resume();
      resolveReady(response.statusCode >= 200 && response.statusCode < 400);
    });
    request.once("timeout", () => request.destroy());
    request.once("error", () => resolveReady(false));
  });
}

async function waitUntilReady(url, running) {
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    if (running.result) throw new Error("Сервер завершился до открытия сайта. Причина указана в сообщениях выше.");
    if (await responds(url)) {
      if (running.result) throw new Error("Сервер остановился. Причина указана в сообщениях выше.");
      return;
    }
    await delay(500);
  }
  throw new Error("Сайт не ответил за 2 минуты. Проверьте сообщения выше и повторите запуск.");
}

async function openBrowser(url) {
  const result = await new Promise((resolveOpened) => {
    // URL содержит только фиксированный локальный адрес и выбранный числовой порт.
    const browser = spawn(`start "" "${url}"`, {
      shell: true,
      stdio: "ignore",
      windowsHide: true,
    });
    browser.once("error", () => resolveOpened(false));
    browser.once("exit", (code) => resolveOpened(code === 0));
  });
  if (!result) console.log(`Браузер не открылся автоматически. Откройте вручную: ${url}`);
}

async function main() {
  assertNodeVersion();
  if (process.platform !== "win32") throw new Error("Этот запускатор предназначен для Windows. На другой ОС используйте npm run dev из каталога web.");

  const stop = () => {
    if (stopping) return;
    stopping = true;
    console.log("\nОстанавливаю сайт…");
    stopOwnedChild();
    process.exit(0);
  };
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);
  process.on("SIGBREAK", stop);

  console.log("NoClip — локальный запуск сайта\n");
  const { fingerprint, needsInstall } = await dependencyState(webDir);
  if (needsInstall) {
    console.log("Устанавливаю зависимости. При первом запуске нужен интернет; это может занять несколько минут.\n");
    const install = startChild(process.env.ComSpec || "cmd.exe", [
      "/d", "/s", "/c", "npm ci --no-audit --no-fund --fetch-retries=1 --fetch-timeout=30000",
    ]);
    const result = await install.done;
    if (result.code !== 0 || result.error) {
      throw new Error("Не удалось установить зависимости. Проверьте доступность npm, подключение к интернету и сообщение npm выше, затем запустите файл ещё раз.");
    }
    await writeFile(join(webDir, "node_modules", stampName), fingerprint);
  }

  const port = await availablePort();
  const url = `http://${host}:${port}`;
  if (port !== 3000) console.log(`Порт 3000 недоступен. Использую порт ${port}.`);
  console.log(`\nЗапускаю ${url}. Окно нужно оставить открытым; для остановки нажмите Ctrl+C.\n`);
  const server = startChild(process.execPath, [
    join(webDir, "node_modules", "next", "dist", "bin", "next"),
    "dev", "--hostname", host, "--port", String(port),
  ]);
  await waitUntilReady(url, server);
  console.log(`\nСайт готов: ${url}\n`);
  await openBrowser(url);
  const result = await server.done;
  if (result.code !== 0 || result.error) throw new Error("Сервер остановился с ошибкой. Причина указана в сообщениях выше.");
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch((error) => {
    stopOwnedChild();
    console.error(`\nОшибка: ${error.message}\n`);
    process.exitCode = 1;
  });
}
