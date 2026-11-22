PHONE_PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>文件传送 V5</title>
  <meta name="referrer" content="no-referrer">
  <style>
    :root { color-scheme: light dark; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; background: #f6f7fb; color: #172033; }
    main { width: min(720px, calc(100vw - 28px)); margin: 0 auto; padding: 22px 0 34px; }
    h1 { font-size: 24px; margin: 6px 0 18px; }
    section { background: #fff; border: 1px solid #dfe4ee; border-radius: 8px; padding: 16px; margin: 14px 0; }
    label { display: block; font-size: 13px; color: #5f6b7a; margin-bottom: 6px; }
    input, button { box-sizing: border-box; width: 100%; font: inherit; border-radius: 7px; }
    input { border: 1px solid #c9d1df; padding: 11px; background: #fff; color: #172033; }
    button { border: 0; padding: 12px; margin-top: 10px; background: #1769e0; color: #fff; font-weight: 650; }
    button.secondary { background: #eef3fb; color: #17355f; }
    .row { display: grid; grid-template-columns: 1fr auto; gap: 8px; align-items: center; padding: 10px 0; border-top: 1px solid #edf0f5; }
    .row:first-child { border-top: 0; }
    .name { word-break: break-all; }
    .muted { color: #6b7685; font-size: 13px; }
    .status { min-height: 22px; margin-top: 10px; color: #17633a; }
    @media (prefers-color-scheme: dark) {
      body { background: #10141c; color: #edf2fb; }
      section { background: #171d28; border-color: #293244; }
      input { background: #10141c; border-color: #3b4658; color: #edf2fb; }
      button.secondary { background: #243044; color: #edf2fb; }
      .row { border-color: #293244; }
    }
  </style>
</head>
<body>
  <main>
    <h1>文件传送 V5</h1>
    <section>
      <label for="code">访问码</label>
      <input id="code" inputmode="numeric" autocomplete="one-time-code" placeholder="输入电脑端显示的 6 位码">
      <button onclick="saveCode()">连接</button>
      <div id="authStatus" class="status"></div>
    </section>
    <section>
      <h2>手机发给电脑</h2>
      <input id="uploadFile" type="file" multiple>
      <button onclick="uploadFiles()">上传到电脑</button>
      <div id="uploadStatus" class="status"></div>
    </section>
    <section>
      <h2>电脑发给手机</h2>
      <button class="secondary" onclick="loadFiles()">刷新文件列表</button>
      <div id="files" class="muted">暂无文件</div>
    </section>
  </main>
  <iframe id="downloadFrame" name="downloadFrame" style="display:none"></iframe>
  <script>
    const codeInput = document.getElementById('code');
    let connected = false;

    function code() {
      return encodeURIComponent(codeInput.value.trim());
    }

    function saveCode() {
      connect().then(() => { loadFiles(); openRoots(); });
    }

    async function connect() {
      const status = document.getElementById('authStatus');
      try {
        const res = await fetch(`/api/connect?code=${code()}`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({device: navigator.userAgent || 'Phone Browser'}),
        });
        if (!res.ok) {
          disconnect('连接失败，请检查访问码');
          return;
        }
        connected = true;
        status.textContent = '已连接电脑';
      } catch (_) {
        disconnect('连接已断开，请输入新的访问码');
      }
    }

    function disconnect(message = '连接已断开，请输入新的访问码') {
      connected = false;
      closePreview();
      libraryRequest++;
      document.getElementById('libraryEntries').replaceChildren();
      document.getElementById('libraryStatus').textContent = '等待重新连接';
      codeInput.value = '';
      document.getElementById('authStatus').textContent = message;
      document.getElementById('files').textContent = '等待重新连接';
    }

    async function loadFiles() {
      if (!connected) return;
      const box = document.getElementById('files');
      let res;
      try {
        res = await fetch(`/api/files?code=${code()}`);
      } catch (_) {
        disconnect();
        return;
      }
      if (res.status === 403) {
        disconnect();
        return;
      }
      if (!res.ok) return;
      const data = await res.json();
      if (!data.files.length) {
        box.textContent = '电脑端还没有选择要分享的文件';
        return;
      }
      box.innerHTML = '';
      for (const file of data.files) {
        const row = document.createElement('div');
        row.className = 'row';
        const name = document.createElement('div');
        name.innerHTML = `<div class="name"></div><div class="muted">${formatSize(file.size)}</div>`;
        name.querySelector('.name').textContent = file.name;
        const button = document.createElement('button');
        button.className = 'secondary';
        button.style.marginTop = '0';
        button.textContent = '下载';
        button.onclick = () => downloadFile(file.id);
        row.append(name, button);
        box.append(row);
      }
    }

    async function downloadFile(fileId) {
      if (!connected) return;
      document.getElementById('downloadFrame').src = `/api/download/${fileId}?code=${code()}&t=${Date.now()}`;
      await connect();
    }

    async function uploadFiles() {
      if (!connected) {
        document.getElementById('uploadStatus').textContent = '请先连接电脑';
        return;
      }
      const input = document.getElementById('uploadFile');
      const status = document.getElementById('uploadStatus');
      if (!input.files.length) {
        status.textContent = '请选择文件';
        return;
      }
      for (const file of input.files) {
        status.textContent = `正在上传 ${file.name}`;
        const res = await fetch(`/api/upload?code=${code()}&filename=${encodeURIComponent(file.name)}`, {
          method: 'POST',
          headers: {'Content-Length': String(file.size)},
          body: file,
        });
        if (res.status === 403) {
          disconnect();
          status.textContent = '连接已断开';
          return;
        }
        if (!res.ok) {
          status.textContent = `${file.name} 上传失败`;
          return;
        }
      }
      status.textContent = '上传完成';
      input.value = '';
    }

    function formatSize(size) {
      if (size < 1024) return `${size} B`;
      if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
      return `${(size / 1024 / 1024).toFixed(1)} MB`;
    }

    setInterval(() => {
      if (connected) loadFiles();
    }, 3000);
  </script>
</body>
</html>"""

from library_ui import LIBRARY_HTML, LIBRARY_SCRIPT

PHONE_PAGE = PHONE_PAGE.replace('    <section>\n      <h2>手机发给电脑</h2>', LIBRARY_HTML + '    <section>\n      <h2>手机发给电脑</h2>')
PHONE_PAGE = PHONE_PAGE.replace('  </script>', LIBRARY_SCRIPT + '  </script>')
