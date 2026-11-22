PHONE_PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>文件传送 V5.1</title>
  <meta name="referrer" content="no-referrer">
  <style>
    :root { color-scheme: light dark; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; background: #f6f7fb; color: #172033; }
    main { width: min(720px, calc(100vw - 28px)); margin: 0 auto; padding: 22px 0 34px; }
    h1 { font-size: 24px; margin: 6px 0 18px; }
    section { background: #fff; border: 1px solid #dfe4ee; border-radius: 8px; padding: 16px; margin: 14px 0; }
    label { display: block; font-size: 13px; color: #5f6b7a; margin-bottom: 6px; }
    input, textarea, button { box-sizing: border-box; width: 100%; font: inherit; border-radius: 7px; }
    input, textarea { border: 1px solid #c9d1df; padding: 11px; background: #fff; color: #172033; }
    textarea { min-height: 110px; resize: vertical; }
    button { border: 0; padding: 12px; margin-top: 10px; background: #1769e0; color: #fff; font-weight: 650; }
    button.secondary { background: #eef3fb; color: #17355f; }
    .actions { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .latest-text { min-height: 68px; white-space: pre-wrap; overflow-wrap: anywhere; padding: 11px; border: 1px solid #dfe4ee; border-radius: 7px; background: #f8f9fc; }
    .open-link { display: block; box-sizing: border-box; width: 100%; margin-top: 10px; padding: 12px; border-radius: 7px; background: #eef3fb; color: #17355f; font-weight: 650; text-align: center; text-decoration: none; }
    .row { display: grid; grid-template-columns: 1fr auto; gap: 8px; align-items: center; padding: 10px 0; border-top: 1px solid #edf0f5; }
    .row:first-child { border-top: 0; }
    .name { word-break: break-all; }
    .muted { color: #6b7685; font-size: 13px; }
    .status { min-height: 22px; margin-top: 10px; color: #17633a; }
    @media (prefers-color-scheme: dark) {
      body { background: #10141c; color: #edf2fb; }
      section { background: #171d28; border-color: #293244; }
      input, textarea { background: #10141c; border-color: #3b4658; color: #edf2fb; }
      button.secondary { background: #243044; color: #edf2fb; }
      .latest-text { background: #10141c; border-color: #3b4658; }
      .open-link { background: #243044; color: #edf2fb; }
      .row { border-color: #293244; }
    }
  </style>
</head>
<body>
  <main>
    <h1>文件传送 V5.1</h1>
    <section>
      <label for="code">访问码</label>
      <input id="code" inputmode="numeric" autocomplete="one-time-code" placeholder="输入电脑端显示的 6 位码">
      <button onclick="saveCode()">连接</button>
      <div id="authStatus" class="status"></div>
    </section>
    <section>
      <h2>文字速传</h2>
      <label for="textInput">粘贴要发送到其他设备的文字或链接</label>
      <textarea id="textInput" maxlength="20000" placeholder="输入文字或链接"></textarea>
      <button onclick="sendText()">发送文字</button>
      <label for="latestText" style="margin-top:14px">最新收到的文字</label>
      <pre id="latestText" class="latest-text">暂无文字</pre>
      <div class="actions">
        <button class="secondary" onclick="copyLatestText()">复制</button>
        <a id="openTextLink" class="open-link" target="_blank" rel="noopener noreferrer" hidden>打开链接</a>
      </div>
      <div id="textStatus" class="status"></div>
    </section>
    <section>
      <h2>手机发给电脑</h2>
      <input id="uploadFile" type="file" multiple>
      <div class="muted" style="margin-top:8px">安卓首次打开文件选择界面可能需要 5～10 秒，这是系统文件管理器的首次加载，请稍候；之后通常会更快。</div>
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
    const latestText = document.getElementById('latestText');
    let connected = false;
    let lastTextVersion = -1;
    let latestTextValue = '';

    function code() {
      return encodeURIComponent(codeInput.value.trim());
    }

    function saveCode() {
      connect().then(() => { loadFiles(); loadLatestText(); openRoots(); });
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
      lastTextVersion = -1;
      latestTextValue = '';
      renderLatestText('');
      setTextStatus('等待重新连接');
    }

    function setTextStatus(message) {
      document.getElementById('textStatus').textContent = message;
    }

    function isSafeHttpUrl(value) {
      const trimmed = value.trim();
      if (!trimmed || /\s/.test(trimmed)) return false;
      try {
        const parsed = new URL(trimmed);
        return parsed.protocol === 'http:' || parsed.protocol === 'https:';
      } catch (_) {
        return false;
      }
    }

    function renderLatestText(value) {
      latestTextValue = value;
      latestText.textContent = value || '暂无文字';
      const link = document.getElementById('openTextLink');
      if (isSafeHttpUrl(value)) {
        link.href = value.trim();
        link.hidden = false;
      } else {
        link.removeAttribute('href');
        link.hidden = true;
      }
    }

    async function loadLatestText() {
      if (!connected) return;
      let res;
      try {
        res = await fetch(`/api/text?code=${code()}`);
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
      if (data.version === lastTextVersion) return;
      lastTextVersion = data.version;
      renderLatestText(data.text || '');
      if (data.version > 0) setTextStatus('已收到最新文字');
    }

    async function sendText() {
      const input = document.getElementById('textInput');
      const text = input.value.trim();
      if (!connected) {
        setTextStatus('请先连接电脑');
        return;
      }
      if (!text) {
        setTextStatus('请输入文字');
        return;
      }
      let res;
      try {
        res = await fetch(`/api/text?code=${code()}`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({text}),
        });
      } catch (_) {
        setTextStatus('发送失败，请检查网络');
        return;
      }
      if (res.status === 403) {
        disconnect();
        return;
      }
      const data = await res.json();
      if (!res.ok) {
        setTextStatus(data.error || '发送失败');
        return;
      }
      input.value = '';
      lastTextVersion = data.version;
      renderLatestText(data.text);
      setTextStatus('已发送');
    }

    async function copyLatestText() {
      if (!latestTextValue) {
        setTextStatus('暂无文字');
        return;
      }
      try {
        await navigator.clipboard.writeText(latestTextValue);
        setTextStatus('已复制');
      } catch (_) {
        setTextStatus('浏览器不允许自动复制，请长按文字手动复制');
      }
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
    setInterval(() => {
      if (connected) loadLatestText();
    }, 2000);
  </script>
</body>
</html>"""

from library_ui import LIBRARY_HTML, LIBRARY_SCRIPT

PHONE_PAGE = PHONE_PAGE.replace('    <section>\n      <h2>手机发给电脑</h2>', LIBRARY_HTML + '    <section>\n      <h2>手机发给电脑</h2>')
PHONE_PAGE = PHONE_PAGE.replace('  </script>', LIBRARY_SCRIPT + '  </script>')

