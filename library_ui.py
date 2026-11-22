LIBRARY_HTML = r'''
    <section aria-labelledby="libraryTitle">
      <h2 id="libraryTitle">电脑共享目录</h2>
      <p class="muted">直接查看电脑里的文件，视频边读边播，无需先完整下载。</p>
      <div style="display:flex;gap:8px">
        <button class="secondary" onclick="openRoots()">共享目录首页</button>
        <button class="secondary" onclick="refreshLibrary()">刷新当前目录</button>
      </div>
      <nav id="breadcrumbs" aria-label="目录位置" style="overflow-wrap:anywhere;margin:12px 0"></nav>
      <div id="libraryStatus" class="status" role="status">连接电脑后显示共享目录</div>
      <div id="libraryEntries"></div>
      <div id="preview" hidden style="margin-top:16px;border-top:1px solid #aaa;padding-top:12px">
        <strong id="previewName" style="overflow-wrap:anywhere"></strong>
        <button class="secondary" onclick="closePreview()">关闭预览 / 停止播放</button>
        <div id="previewBody" style="margin-top:12px"></div>
        <p id="previewStatus" class="muted" role="status"></p>
        <a id="previewDownload" target="downloadFrame">下载原文件</a>
      </div>
    </section>
'''

LIBRARY_SCRIPT = r'''
    let libraryRoot = null;
    let libraryPath = '';
    let libraryRequest = 0;
    let previewActive = false;

    function libraryUrl(endpoint, path = libraryPath) {
      return `/api/library/${endpoint}?code=${code()}&root=${encodeURIComponent(libraryRoot ? libraryRoot.id : '')}&path=${encodeURIComponent(path)}`;
    }

    async function libraryFetch(url) {
      const response = await fetch(url, {cache: 'no-store'});
      if (response.status === 403) {
        // File permissions also produce 403; only authentication errors disconnect.
        const data = await response.json();
        if (data.error === 'Invalid access code' || data.error === 'Connection expired') disconnect();
        throw new Error(data.error || '没有访问权限');
      }
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error || '读取失败，请刷新重试');
      }
      return response.json();
    }

    function libraryRow(label, detail, action, actionText) {
      const row = document.createElement('div');
      row.className = 'row';
      const text = document.createElement('div');
      text.className = 'name';
      text.textContent = label;
      const subtitle = document.createElement('div');
      subtitle.className = 'muted';
      subtitle.textContent = detail;
      text.append(subtitle);
      const button = document.createElement('button');
      button.className = 'secondary';
      button.textContent = actionText;
      button.style.marginTop = '0';
      button.onclick = action;
      row.append(text, button);
      return row;
    }

    function renderBreadcrumbs() {
      const nav = document.getElementById('breadcrumbs');
      nav.replaceChildren();
      if (!libraryRoot) return;
      function crumb(label, path) {
        const button = document.createElement('button');
        button.className = 'secondary';
        button.style.cssText = 'width:auto;padding:6px 10px;margin:3px';
        button.textContent = label;
        button.onclick = () => openFolder(path);
        nav.append(button);
      }
      crumb(libraryRoot.name, '');
      let path = '';
      for (const part of libraryPath.split('/').filter(Boolean)) {
        path = path ? path + '/' + part : part;
        crumb(part, path);
      }
    }

    async function openRoots() {
      closePreview();
      libraryRoot = null;
      libraryPath = '';
      renderBreadcrumbs();
      const box = document.getElementById('libraryEntries');
      box.replaceChildren();
      const status = document.getElementById('libraryStatus');
      if (!connected) { status.textContent = '请先输入访问码连接电脑'; return; }
      const request = ++libraryRequest;
      status.textContent = '正在读取共享目录…';
      try {
        const data = await libraryFetch(libraryUrl('roots'));
        if (request !== libraryRequest || !connected) return;
        status.textContent = data.roots.length ? '选择一个文件夹' : '电脑尚未开放目录，请在电脑端点击“添加共享文件夹”';
        for (const root of data.roots) {
          box.append(libraryRow('📁 ' + root.name, root.available ? '只读共享目录' : '磁盘离线或目录不可用', () => {
            libraryRoot = root;
            openFolder('');
          }, '浏览'));
        }
      } catch (error) { if (request === libraryRequest) status.textContent = error.message || '无法连接电脑'; }
    }

    async function openFolder(path) {
      if (!connected || !libraryRoot) return;
      closePreview();
      libraryPath = path;
      renderBreadcrumbs();
      const box = document.getElementById('libraryEntries');
      box.replaceChildren();
      const status = document.getElementById('libraryStatus');
      const request = ++libraryRequest;
      status.textContent = '正在读取…';
      try {
        const data = await libraryFetch(libraryUrl('list'));
        if (request !== libraryRequest || !connected) return;
        status.textContent = data.entries.length ? `${data.entries.length} 个项目` : '此文件夹为空';
        for (const entry of data.entries) {
          const label = entry.directory ? '📁 ' + entry.name : entry.name;
          box.append(libraryRow(label, entry.directory ? '文件夹' : formatSize(entry.size), () => {
            if (entry.directory) openFolder(entry.path); else openPreview(entry);
          }, entry.directory ? '打开' : entry.kind === 'download' ? '文件' : '查看'));
        }
      } catch (error) { if (request === libraryRequest) status.textContent = error.message || '无法连接电脑'; }
    }

    function refreshLibrary() {
      if (libraryRoot) openFolder(libraryPath); else openRoots();
    }

    function closePreview() {
      previewActive = false;
      const body = document.getElementById('previewBody');
      for (const media of body.querySelectorAll('video, audio')) {
        media.pause();
        media.removeAttribute('src');
        media.load();
      }
      body.replaceChildren();
      document.getElementById('preview').hidden = true;
      document.getElementById('previewDownload').removeAttribute('href');
    }

    function openPreview(entry) {
      closePreview();
      if (!connected) return;
      const preview = document.getElementById('preview');
      preview.hidden = false;
      document.getElementById('previewName').textContent = entry.name;
      const status = document.getElementById('previewStatus');
      const url = libraryUrl('file', entry.path);
      document.getElementById('previewDownload').href = url + '&download=1';
      if (entry.kind === 'download') {
        status.textContent = '此格式暂不支持网页预览，可下载后用对应应用打开。';
      } else {
        const media = document.createElement(entry.kind === 'image' ? 'img' : entry.kind);
        media.style.cssText = 'display:block;width:100%;max-height:65vh;object-fit:contain;background:#111';
        if (entry.kind === 'image') {
          media.alt = entry.name;
          status.textContent = '正在加载图片…';
          media.onload = () => { status.textContent = '图片来自电脑共享目录'; };
        } else {
          media.controls = true;
          media.preload = 'metadata';
          media.setAttribute('playsinline', '');
          status.textContent = '点击播放，支持拖动进度；能否播放取决于手机支持的视频和音频编码。';
          media.onplay = () => { previewActive = true; keepLibraryAlive(); };
          media.onpause = () => { previewActive = false; };
          media.onended = () => { previewActive = false; };
        }
        media.onerror = () => {
          previewActive = false;
          status.textContent = '无法预览：可能是手机不支持此编码、文件已停止共享或网络已断开。V5 不自动转码，可下载后用播放器打开。';
        };
        media.src = url;
        document.getElementById('previewBody').append(media);
      }
      preview.scrollIntoView({behavior: 'smooth', block: 'nearest'});
    }

    async function keepLibraryAlive() {
      if (!connected || !previewActive) return;
      try {
        const response = await fetch(`/api/library/heartbeat?code=${code()}`, {method: 'POST'});
        if (response.status === 403) disconnect('连接已过期，请重新输入访问码');
      } catch (_) { /* A transient loss can recover on the next media request. */ }
    }
    setInterval(keepLibraryAlive, 30000);
'''
