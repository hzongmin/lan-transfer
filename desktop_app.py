import os
import socket
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from uuid import uuid4

import qrcode
from PIL import ImageTk

from discovery import DiscoveryService
from network_utils import choose_preferred_address, discover_addresses
from remote_client import RemoteSendError, connect_remote, normalize_base_url, send_files_to_remote
from server import create_http_server
from transfer_state import TransferState
from shared_library import SharedLibrary

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES = None
    TkinterDnD = None


DEFAULT_PORT = 8765


BaseTk = TkinterDnD.Tk if TkinterDnD is not None else tk.Tk


class DesktopApp(BaseTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("文件传送V5")
        self.geometry("800x680")
        self.minsize(720, 620)

        receive_dir = Path.home() / "Downloads" / "LanTransfer"
        self.state = TransferState(receive_dir=receive_dir, event_callback=self._on_event)
        config_dir = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'LanTransferV5'
        self.state.library = SharedLibrary(config_dir / 'shared-folders.json')
        self.httpd = None
        self.server_thread = None
        self.discovery_service = None
        self.device_id = uuid4().hex
        self.device_name = socket.gethostname() or "LanTransfer PC"
        self.nearby_device_labels = {}
        self.addresses = discover_addresses()
        self.selected_address = choose_preferred_address(self.addresses)
        self.local_ip = self.selected_address.ip
        self.port = DEFAULT_PORT

        self.url_var = tk.StringVar(value="")
        self.code_var = tk.StringVar(value=self.state.access_code)
        self.dir_var = tk.StringVar(value=str(receive_dir))
        self.status_var = tk.StringVar(value="服务器未启动")
        self.address_var = tk.StringVar(value=self.selected_address.label)
        self.remote_url_var = tk.StringVar(value="")
        self.remote_code_var = tk.StringVar(value="")
        self.remote_file_var = tk.StringVar(value="")
        self.remote_file_paths = []
        self.remote_status_var = tk.StringVar(value="未连接对方电脑")
        self.nearby_device_var = tk.StringVar(value="")
        self.remote_encrypted_var = tk.BooleanVar(value=False)
        self.remote_encryption_status_var = tk.StringVar(value="普通传送：未启用加密，断点续传、SHA256 校验、自动重试已启用")
        self.connected_remote_url = ""
        self.connected_remote_code = ""
        self.qr_image = None
        self.qr_revision = uuid4().hex

        self._build_ui()
        if self.state.library.load_error:
            messagebox.showwarning('共享配置读取失败', '共享目录配置无法读取，请重新添加目录。原配置会在添加目录时更新。')
        self._start_server()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        pad = {"padx": 14, "pady": 8}

        shell = ttk.Frame(self)
        shell.pack(fill="both", expand=True)
        canvas = tk.Canvas(shell, highlightthickness=0)
        page_scroll = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=page_scroll.set)
        page_scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        content = ttk.Frame(canvas)
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")

        def sync_scroll_region(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def sync_width(event):
            canvas.itemconfigure(content_window, width=event.width)

        content.bind("<Configure>", sync_scroll_region)
        canvas.bind("<Configure>", sync_width)
        canvas.bind_all("<MouseWheel>", lambda event: canvas.yview_scroll(int(-1 * (event.delta / 120)), "units"))

        header = ttk.Frame(content)
        header.pack(fill="x", **pad)
        ttk.Label(header, text="手机扫码或浏览器打开这个地址", font=("", 13, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Entry(header, textvariable=self.url_var, state="readonly").grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ttk.Button(header, text="复制地址", command=self._copy_url).grid(row=1, column=1, padx=(8, 0))
        ttk.Button(header, text="打开网页", command=self._open_url).grid(row=1, column=2, padx=(8, 0))
        ttk.Label(header, text="如果开了 VPN，请选择 Wi-Fi/以太网的 192.168.x.x 地址").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.address_box = ttk.Combobox(
            header,
            textvariable=self.address_var,
            values=[address.label for address in self.addresses],
            state="readonly",
        )
        self.address_box.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        self.address_box.bind("<<ComboboxSelected>>", self._on_address_selected)
        header.columnconfigure(0, weight=1)

        qr_frame = ttk.Frame(content)
        qr_frame.pack(fill="x", **pad)
        self.qr_label = ttk.Label(qr_frame)
        self.qr_label.pack(side="left")
        qr_actions = ttk.Frame(qr_frame)
        qr_actions.pack(side="left", padx=(14, 0), anchor="n")
        ttk.Label(qr_actions, text="手机相机扫码后输入访问码即可连接").pack(anchor="w")
        ttk.Button(qr_actions, text="刷新二维码 / 访问码", command=self._refresh_connection_code).pack(anchor="w", pady=(12, 6))
        ttk.Label(qr_actions, text="刷新后，已连接设备需用新访问码重新连接。\n正在传输时请等传完再刷新。", wraplength=360).pack(anchor="w")

        auth = ttk.Frame(content)
        auth.pack(fill="x", **pad)
        ttk.Label(auth, text="访问码", font=("", 12, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(auth, textvariable=self.code_var, font=("", 22, "bold")).grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Label(auth, text="手机首次进入网页时输入这个 6 位码").grid(row=0, column=2, sticky="w", padx=(12, 0))
        auth.columnconfigure(2, weight=1)

        clients = ttk.LabelFrame(content, text="设备连接状态")
        clients.pack(fill="x", **pad)
        self.client_status_var = tk.StringVar(value="还没有设备连接")
        ttk.Label(clients, textvariable=self.client_status_var).pack(anchor="w", padx=8, pady=8)

        folder = ttk.LabelFrame(content, text="接收目录")
        folder.pack(fill="x", **pad)
        ttk.Entry(folder, textvariable=self.dir_var, state="readonly").grid(row=0, column=0, sticky="ew", padx=8, pady=10)
        ttk.Button(folder, text="更换", command=self._choose_receive_dir).grid(row=0, column=1, padx=(0, 8), pady=10)
        ttk.Button(folder, text="打开", command=self._open_receive_dir).grid(row=0, column=2, padx=(0, 8), pady=10)
        folder.columnconfigure(0, weight=1)

        library = ttk.LabelFrame(content, text="共享目录 · 手机直接查看 / 播放（只读）")
        library.pack(fill="x", **pad)
        actions = ttk.Frame(library)
        actions.pack(fill="x", padx=8, pady=8)
        ttk.Button(actions, text="添加共享文件夹", command=self._add_library_folder).pack(side="left")
        ttk.Button(actions, text="停止共享选中目录", command=self._remove_library_folder).pack(side="left", padx=8)
        self.library_list = tk.Listbox(library, height=4, exportselection=False)
        self.library_list.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(library, text="手机连接后进入“电脑共享目录”；文件留在电脑，视频按需读取。目录会自动记住。", wraplength=650).pack(anchor="w", padx=8, pady=(0, 8))
        self._refresh_library_folders()

        share = ttk.LabelFrame(content, text="电脑发给手机")
        share.pack(fill="x", padx=14, pady=6)
        toolbar = ttk.Frame(share)
        toolbar.pack(fill="x", padx=8, pady=8)
        ttk.Button(toolbar, text="添加到待发送", command=self._add_pending_files).pack(side="left")
        ttk.Button(toolbar, text="发送到手机", command=self._publish_to_phone).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="清空列表", command=self._clear_shared_files).pack(side="left", padx=(8, 0))
        shared_box = ttk.Frame(share)
        shared_box.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.shared_list = tk.Listbox(shared_box, height=5)
        shared_scroll = ttk.Scrollbar(shared_box, orient="vertical", command=self.shared_list.yview)
        self.shared_list.configure(yscrollcommand=shared_scroll.set)
        self.shared_list.pack(side="left", fill="both", expand=True)
        shared_scroll.pack(side="right", fill="y")
        self.drop_hint = ttk.Label(share, text="可点“添加到待发送”选择文件；若系统支持拖拽，也可把文件拖到这里")
        self.drop_hint.pack(anchor="w", padx=8, pady=(0, 8))
        self._enable_drop_target()

        remote = ttk.LabelFrame(content, text="电脑传电脑")
        remote.pack(fill="x", padx=14, pady=6)
        ttk.Label(remote, text="附近电脑").grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))
        self.nearby_box = ttk.Combobox(remote, textvariable=self.nearby_device_var, values=[], state="readonly")
        self.nearby_box.grid(row=0, column=1, columnspan=3, sticky="ew", padx=8, pady=(8, 4))
        ttk.Button(remote, text="使用选中设备", command=self._use_selected_nearby_device).grid(row=0, column=4, padx=8, pady=(8, 4))
        ttk.Label(remote, text="对方地址").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(remote, textvariable=self.remote_url_var).grid(row=1, column=1, columnspan=4, sticky="ew", padx=8, pady=4)
        ttk.Label(remote, text="对方访问码").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(remote, textvariable=self.remote_code_var, width=12).grid(row=2, column=1, sticky="w", padx=8, pady=4)
        ttk.Button(remote, text="确认连接", command=self._connect_remote_pc).grid(row=2, column=2, padx=8, pady=4)
        ttk.Button(remote, text="断开连接", command=self._disconnect_remote_pc).grid(row=2, column=3, padx=8, pady=4)
        pick_frame = ttk.Frame(remote)
        pick_frame.grid(row=2, column=4, padx=8, pady=4)
        ttk.Button(pick_frame, text="选择文件", command=self._choose_remote_file).pack(side="left")
        ttk.Button(pick_frame, text="选择文件夹", command=self._choose_remote_folder).pack(side="left", padx=(6, 0))
        ttk.Entry(remote, textvariable=self.remote_file_var, state="readonly").grid(row=3, column=0, columnspan=4, sticky="ew", padx=8, pady=4)
        ttk.Button(remote, text="发送到对方电脑", command=self._send_to_remote_pc).grid(row=3, column=4, padx=8, pady=4)
        ttk.Checkbutton(
            remote,
            text="加密传送",
            variable=self.remote_encrypted_var,
            command=self._update_remote_encryption_status,
        ).grid(row=4, column=0, sticky="w", padx=8, pady=4)
        ttk.Label(remote, textvariable=self.remote_encryption_status_var).grid(row=4, column=1, columnspan=4, sticky="w", padx=8, pady=4)
        ttk.Label(remote, textvariable=self.remote_status_var).grid(row=5, column=0, columnspan=5, sticky="w", padx=8, pady=(0, 8))
        remote.columnconfigure(1, weight=1)

        log_frame = ttk.LabelFrame(content, text="传输日志")
        log_frame.pack(fill="x", padx=14, pady=6)
        log_box = ttk.Frame(log_frame)
        log_box.pack(fill="both", expand=True, padx=8, pady=8)
        self.log_list = tk.Listbox(log_box, height=6)
        log_scroll = ttk.Scrollbar(log_box, orient="vertical", command=self.log_list.yview)
        self.log_list.configure(yscrollcommand=log_scroll.set)
        self.log_list.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        footer = ttk.Frame(content)
        footer.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Label(footer, textvariable=self.status_var).pack(side="left")

    def _start_server(self) -> None:
        for port in range(DEFAULT_PORT, DEFAULT_PORT + 20):
            try:
                self.httpd = create_http_server(("0.0.0.0", port), self.state)
                self.port = port
                break
            except OSError:
                continue
        if self.httpd is None:
            messagebox.showerror("启动失败", "没有可用端口")
            self.destroy()
            return

        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()
        url = f"http://{self.local_ip}:{self.port}"
        self._set_url(url)
        self.status_var.set(f"服务器运行中：{url}")
        self.state.add_event("Server started")
        self._start_discovery()

    def _start_discovery(self) -> None:
        if self.discovery_service is not None:
            return
        self.discovery_service = DiscoveryService(
            self.device_id,
            self.device_name,
            lambda: self.url_var.get(),
        )
        self.discovery_service.start()
        self.state.add_event("Device discovery started")
        self.after(2000, self._refresh_nearby_devices)

    def _refresh_nearby_devices(self) -> None:
        if self.discovery_service is None:
            return
        labels = {}
        for device in self.discovery_service.devices():
            label = f"{device.name} - {device.url}"
            labels[label] = device
        self.nearby_device_labels = labels
        values = list(labels.keys())
        self.nearby_box.configure(values=values)
        if self.nearby_device_var.get() not in labels:
            self.nearby_device_var.set(values[0] if values else "")
        self.after(3000, self._refresh_nearby_devices)

    def _use_selected_nearby_device(self) -> None:
        device = self.nearby_device_labels.get(self.nearby_device_var.get())
        if device is None:
            messagebox.showinfo("未发现电脑", "还没有发现同一网络内的其他电脑，也可以手动输入对方地址")
            return
        self.remote_url_var.set(device.url)
        self.connected_remote_url = ""
        self.connected_remote_code = ""
        self.remote_status_var.set(f"已填入附近电脑：{device.name}，请确认连接")
        self.state.add_event(f"Selected nearby PC {device.name} ({device.url})")

    def _on_address_selected(self, _event=None) -> None:
        selected = self.address_var.get()
        for address in self.addresses:
            if address.label == selected:
                self.selected_address = address
                self.local_ip = address.ip
                url = f"http://{self.local_ip}:{self.port}"
                self._set_url(url)
                self.status_var.set(f"已切换地址：{url}")
                self.state.add_event(f"Address changed to {url}")
                return

    def _set_url(self, url: str) -> None:
        self.url_var.set(url)
        self._render_qr(url)

    def _refresh_connection_code(self) -> None:
        self.code_var.set(self.state.refresh_access_code())
        self.qr_revision = uuid4().hex
        self._render_qr(self.url_var.get())
        self._refresh_client_status()
        self.status_var.set("二维码和访问码已刷新，请手机重新扫码并输入新访问码")

    def _render_qr(self, url: str) -> None:
        qr = qrcode.QRCode(box_size=5, border=2)
        qr.add_data(f"{url}?v={self.qr_revision}")
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        self.qr_image = ImageTk.PhotoImage(image)
        self.qr_label.configure(image=self.qr_image)

    def _copy_url(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.url_var.get())
        self.status_var.set("地址已复制")

    def _open_url(self) -> None:
        webbrowser.open(self.url_var.get())

    def _choose_receive_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.dir_var.get())
        if not selected:
            return
        self.state.set_receive_dir(Path(selected))
        self.dir_var.set(selected)

    def _open_receive_dir(self) -> None:
        Path(self.dir_var.get()).mkdir(parents=True, exist_ok=True)
        os.startfile(self.dir_var.get())

    def _add_pending_files(self) -> None:
        files = filedialog.askopenfilenames()
        self._queue_files(files)

    def _queue_files(self, files) -> None:
        for item in files:
            try:
                self.state.add_pending_file(Path(item))
            except OSError as exc:
                messagebox.showwarning("无法添加", str(exc))
        self._refresh_shared_list()

    def _publish_to_phone(self) -> None:
        self.state.publish_pending_files()
        self._refresh_shared_list()

    def _clear_shared_files(self) -> None:
        self.state.clear_shared_files()
        self._refresh_shared_list()

    def _refresh_shared_list(self) -> None:
        self.shared_list.delete(0, tk.END)
        pending = self.state.pending_files_json()
        published = self.state.shared_files_json()
        if pending:
            self.shared_list.insert(tk.END, "待发送：")
            for item in pending:
                self.shared_list.insert(tk.END, f"  {item['name']} ({format_size(int(item['size']))})")
        if published:
            if pending:
                self.shared_list.insert(tk.END, "")
            self.shared_list.insert(tk.END, "手机可下载：")
            for item in published:
                self.shared_list.insert(tk.END, f"  {item['name']} ({format_size(int(item['size']))})")

    def _enable_drop_target(self) -> None:
        if DND_FILES is None:
            return
        self.shared_list.drop_target_register(DND_FILES)
        self.shared_list.dnd_bind("<<Drop>>", self._on_drop_files)

    def _on_drop_files(self, event) -> None:
        files = self.tk.splitlist(event.data)
        self._queue_files(files)

    def _choose_remote_file(self) -> None:
        selected = filedialog.askopenfilenames()
        if not selected:
            return
        self.remote_file_paths = [str(item) for item in selected]
        self.remote_file_var.set(describe_paths(self.remote_file_paths))

    def _choose_remote_folder(self) -> None:
        selected = filedialog.askdirectory()
        if not selected:
            return
        self.remote_file_paths = [selected]
        self.remote_file_var.set(f"文件夹：{Path(selected).name}")

    def _update_remote_encryption_status(self) -> None:
        if self.remote_encrypted_var.get():
            text = "加密传送已开启：X25519 + AES-GCM，断点续传、SHA256 校验、自动重试已启用"
        else:
            text = "普通传送：未启用加密，断点续传、SHA256 校验、自动重试已启用"
        self.remote_encryption_status_var.set(text)
        self.state.add_event(text)

    def _send_to_remote_pc(self) -> None:
        remote_url = self.remote_url_var.get().strip()
        remote_code = self.remote_code_var.get().strip()
        remote_files = list(self.remote_file_paths)
        encrypted = self.remote_encrypted_var.get()
        if not remote_url or not remote_code or not remote_files:
            messagebox.showwarning("信息不完整", "请填写对方地址、访问码，并选择文件")
            return
        normalized = normalize_base_url(remote_url)
        if normalized != self.connected_remote_url or remote_code != self.connected_remote_code:
            messagebox.showwarning("请先确认连接", "请先点击“确认连接”，成功后再发送文件")
            return
        threading.Thread(
            target=self._send_to_remote_worker,
            args=(normalized, remote_code, remote_files, encrypted),
            daemon=True,
        ).start()

    def _disconnect_remote_pc(self) -> None:
        self.connected_remote_url = ""
        self.connected_remote_code = ""
        self.remote_status_var.set("已断开对方电脑连接")
        self.state.add_event("Disconnected remote PC by user")
        self._refresh_client_status()

    def _connect_remote_pc(self) -> None:
        remote_url = self.remote_url_var.get().strip()
        remote_code = self.remote_code_var.get().strip()
        if not remote_url or not remote_code:
            messagebox.showwarning("信息不完整", "请填写对方地址和访问码")
            return
        threading.Thread(
            target=self._connect_remote_worker,
            args=(remote_url, remote_code),
            daemon=True,
        ).start()

    def _connect_remote_worker(self, remote_url: str, remote_code: str) -> None:
        normalized = normalize_base_url(remote_url)
        self._on_event(f"Connecting remote PC {normalized}")
        try:
            result = connect_remote(normalized, remote_code)
        except RemoteSendError as exc:
            self.connected_remote_url = ""
            self.connected_remote_code = ""
            self.after(0, lambda: self.remote_status_var.set("对方电脑连接失败"))
            self._on_event(f"Remote PC connect failed: {exc}")
            self.after(0, lambda: messagebox.showerror("连接失败", str(exc)))
            return
        self.connected_remote_url = normalized
        self.connected_remote_code = remote_code
        encryption_note = "，支持加密传送" if result.get("encryptionPublicKey") else ""
        self.after(0, lambda: self.remote_status_var.set(f"已连接对方电脑：{normalized}{encryption_note}，支持断点续传和校验"))
        self._on_event(f"Connected remote PC {normalized}")
        self.after(0, self._refresh_client_status)

    def _send_to_remote_worker(self, remote_url: str, remote_code: str, remote_files, encrypted: bool) -> None:
        paths = [Path(item) for item in remote_files]
        mode = "encrypted" if encrypted else "plain"
        summary = describe_paths(paths)
        self._on_event(f"Sending {summary} to remote PC ({mode})")

        def progress(event) -> None:
            text = (
                f"正在发送 {event['fileIndex']}/{event['fileCount']}：{event['fileName']} "
                f"{format_size(int(event['totalBytesSent']))}/{format_size(int(event['totalBytes']))}"
            )
            self.after(0, lambda: self.remote_status_var.set(text))

        try:
            send_files_to_remote(remote_url, remote_code, paths, encrypted=encrypted, progress_callback=progress)
        except (OSError, ValueError, RemoteSendError) as exc:
            self._on_event(f"Remote send failed: {exc}")
            self.after(0, lambda: messagebox.showerror("发送失败", str(exc)))
            return
        self.after(0, lambda: self.remote_status_var.set(f"发送完成并校验：{summary}"))
        self._on_event(f"Sent {summary} to remote PC ({mode})")

    def _on_event(self, message: str) -> None:
        self.after(0, lambda: self._append_log(message))

    def _append_log(self, message: str) -> None:
        self.log_list.insert(tk.END, message)
        self.log_list.see(tk.END)
        self._refresh_client_status()

    def _refresh_client_status(self) -> None:
        clients = self.state.connected_clients_json()
        remote_text = self.remote_status_var.get()
        if not clients:
            self.client_status_var.set(remote_text if self.connected_remote_url else "还没有设备连接")
            return
        latest = clients[0]
        count = len(clients)
        local_text = f"已连接来访设备 {count} 台：{latest['device']} / {latest['ip']}"
        if self.connected_remote_url:
            self.client_status_var.set(f"{local_text}；{remote_text}")
        else:
            self.client_status_var.set(local_text)

    def _refresh_library_folders(self):
        self.library_roots = self.state.library.roots(local=True)
        self.library_list.delete(0, tk.END)
        for root in self.library_roots:
            self.library_list.insert(tk.END, root['localPath'] + ('' if root['available'] else '（离线）'))

    def _add_library_folder(self):
        folder = filedialog.askdirectory(title='选择允许手机只读访问的文件夹')
        if not folder:
            return
        try:
            self.state.library.add(folder)
            self._refresh_library_folders()
        except (OSError, ValueError, RuntimeError) as exc:
            messagebox.showerror('无法共享', str(exc))

    def _remove_library_folder(self):
        selection = self.library_list.curselection()
        if not selection:
            return
        try:
            self.state.library.remove(self.library_roots[selection[0]]['id'])
            self._refresh_library_folders()
        except (OSError, KeyError) as exc:
            messagebox.showerror('无法停止共享', str(exc))

    def _on_close(self) -> None:
        if self.discovery_service is not None:
            self.discovery_service.stop()
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        self.destroy()


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def describe_paths(paths) -> str:
    normalized = [Path(item) for item in paths]
    if not normalized:
        return "未选择文件"
    if len(normalized) == 1:
        return normalized[0].name
    preview = "、".join(path.name for path in normalized[:2])
    if len(normalized) > 2:
        preview += f" 等 {len(normalized)} 个文件"
    else:
        preview = f"{len(normalized)} 个文件：{preview}"
    return preview


def main() -> None:
    app = DesktopApp()
    app.mainloop()
