#!/usr/bin/env python3
"""Xbox 360 Content Manager — v2.

CLI:
  scan, unlock, upload, restore, tu-download, tu-upload, fatx-list, fatx-extract
GUI: run with no arguments.
"""

import argparse
import os
import sys
import time

# ---------------------------------------------------------------------------
# Shared scanning / uploading logic
# ---------------------------------------------------------------------------

def scan_paths(paths):
    from stfs import is_stfs_file, read_stfs_header
    from svod import is_god_folder, read_god_header

    results = []
    file_paths = []
    for p in paths:
        if os.path.isfile(p):
            file_paths.append(p)
        elif os.path.isdir(p):
            if is_god_folder(p):
                results.append(("god", p, read_god_header(p)))
                continue
            for root, dirs, names in os.walk(p):
                if is_god_folder(root):
                    results.append(("god", root, read_god_header(root)))
                    dirs[:] = []
                    continue
                for n in names:
                    full = os.path.join(root, n)
                    if is_stfs_file(full):
                        file_paths.append(full)

    for f in file_paths:
        h = read_stfs_header(f)
        if h:
            results.append(("stfs", f, h))
    return results


def unlock_item(kind, path, make_backup: bool = True):
    from stfs import unlock_stfs_safe
    from svod import unlock_god
    if kind == "stfs":
        return unlock_stfs_safe(path, make_backup=make_backup)
    if kind == "god":
        # For GOD, back up the header file specifically.
        if make_backup:
            from svod import find_god_header
            from stfs import backup_file
            hp = find_god_header(path)
            if hp:
                backup_file(hp)
        return unlock_god(path)
    return False


def upload_items(items, host, port, user, password, workers=1,
                 verify=True, progress=None):
    """Upload items (legacy single-worker path, kept for compatibility)."""
    from ftp_client import XboxFTPClient, prepare_upload_plan, upload_file_verified

    plan = prepare_upload_plan(items)

    if workers > 1 and len(plan) > 1:
        from ftp_client import upload_many_parallel
        ok, fail, errors = upload_many_parallel(
            plan, host, port, user, password,
            workers=workers, verify=verify,
            progress=lambda lp, rp, s, t: progress and progress(lp, 0, t) if progress else None,
        )
        msg = f"Uploaded {ok} file(s)." + (f" {fail} failed." if fail else "")
        return fail == 0, msg

    client = XboxFTPClient()
    if not client.connect(host, port, user, password):
        return False, "FTP connection failed"
    try:
        total = len(plan)
        for idx, (local, remote) in enumerate(plan):
            if progress:
                progress(local, idx + 1, total)
            if not upload_file_verified(client, local, remote, verify=verify):
                return False, f"Failed uploading {local}"
        return True, f"Uploaded {total} file(s)"
    finally:
        client.disconnect()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cli_main(args):
    if args.command == "scan":
        items = scan_paths(args.paths)
        if not items:
            print("No content found.")
            return 0
        print(f"Found {len(items)} item(s):\n")
        for kind, path, h in items:
            print(f"  [{kind.upper():4}] {h.display_name or h.title_name or '?'}")
            print(f"         TitleID : {h.title_id_hex}")
            print(f"         MediaID : {h.media_id_hex}")
            print(f"         Type    : {h.content_type_name}")
            print(f"         Path    : {path}\n")
        return 0

    if args.command == "unlock":
        items = scan_paths(args.paths)
        ok = fail = 0
        for kind, path, h in items:
            name = h.display_name or h.title_name or os.path.basename(path)
            if unlock_item(kind, path, make_backup=not args.no_backup):
                print(f"[OK]   {name}")
                ok += 1
            else:
                print(f"[FAIL] {name}")
                fail += 1
        print(f"\nUnlocked {ok}, failed {fail}.")
        return 0 if fail == 0 else 1

    if args.command == "restore":
        from stfs import restore_backup
        items = scan_paths(args.paths)
        ok = fail = 0
        for kind, path, h in items:
            target = path
            if kind == "god":
                from svod import find_god_header
                target = find_god_header(path) or path
            if restore_backup(target):
                print(f"[OK]   restored {target}")
                ok += 1
            else:
                print(f"[SKIP] no backup for {target}")
                fail += 1
        print(f"\nRestored {ok}, skipped {fail}.")
        return 0

    if args.command == "upload":
        items = scan_paths(args.paths)
        if not items:
            print("No content found.")
            return 1

        def p(label, cur, tot):
            print(f"  [{cur}/{tot}] {os.path.basename(label)}")

        ok, msg = upload_items(
            items, args.host, args.port, args.user, args.password,
            workers=args.workers, verify=not args.no_verify, progress=p,
        )
        print(msg)
        return 0 if ok else 1

    if args.command == "tu-download":
        from xboxunity import XboxUnityClient
        client = XboxUnityClient()
        tu = client.get_latest_title_update(args.media_id)
        if not tu:
            print(f"No Title Update found for MediaID {args.media_id}")
            return 1
        print(f"Downloading TU v{tu.version} for {tu.title_id}...")
        path = client.download_title_update(tu, args.out)
        print(f"Saved to {path}" if path else "Download failed")
        return 0 if path else 1

    if args.command == "tu-upload":
        from xboxunity import XboxUnityClient
        from ftp_client import XboxFTPClient
        client = XboxUnityClient()
        tu = client.get_latest_title_update(args.media_id)
        if not tu:
            print(f"No Title Update found for MediaID {args.media_id}")
            return 1
        path = client.download_title_update(tu, args.out or ".")
        if not path:
            return 1
        ftp = XboxFTPClient()
        if not ftp.connect(args.host, args.port, args.user, args.password):
            print("FTP connection failed.")
            return 1
        remote = f"Content/0000000000000000/{args.title_id}/000B0000/{os.path.basename(path)}"
        ok = ftp.upload_file(path, remote)
        ftp.disconnect()
        print(f"{'Uploaded' if ok else 'Failed'} {remote}")
        return 0 if ok else 1

    if args.command == "fatx-list":
        from fatx import FatxVolume, FatxError
        try:
            with FatxVolume(args.device, offset=args.offset) as vol:
                for path, entry in vol.walk():
                    kind = "DIR " if entry.is_directory else "FILE"
                    print(f"  {kind} {entry.size:>12} {path}")
        except FatxError as e:
            print(f"Error: {e}")
            return 1
        return 0

    if args.command == "fatx-extract":
        from fatx import FatxVolume, FatxError
        try:
            with FatxVolume(args.device, offset=args.offset) as vol:
                matched = [(p, e) for p, e in vol.walk()
                           if args.filter.lower() in p.lower() and not e.is_directory]
                if not matched:
                    print("No matching files.")
                    return 1
                os.makedirs(args.out, exist_ok=True)
                for p, e in matched:
                    rel = p.lstrip("/").replace("/", os.sep)
                    dst = os.path.join(args.out, rel)
                    ok = vol.read_file_to(e, dst)
                    print(f"[{'OK' if ok else 'FAIL'}] {p}")
        except FatxError as e:
            print(f"Error: {e}")
            return 1
        return 0

    return 0


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

def gui_main():
    try:
        from PySide6.QtCore import (Qt, QThread, Signal, QObject, QSettings,
                                     QMimeData, QTimer)
        from PySide6.QtWidgets import (
            QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
            QPushButton, QLabel, QLineEdit, QTableWidget, QTableWidgetItem,
            QFileDialog, QMessageBox, QProgressBar, QHeaderView, QGroupBox,
            QSpinBox, QStatusBar, QAbstractItemView, QTextEdit, QSplitter,
            QMenu, QCheckBox, QToolBar,
        )
        from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent
    except ImportError:
        print("PySide6 is required for GUI mode. pip install PySide6")
        sys.exit(1)

    from stfs import is_unlocked
    from svod import is_god_unlocked

    # ------------------------------------------------------------------
    # Workers
    # ------------------------------------------------------------------

    class ScanWorker(QObject):
        finished = Signal(list)
        error = Signal(str)

        def __init__(self, paths):
            super().__init__()
            self.paths = paths

        def run(self):
            try:
                self.finished.emit(scan_paths(self.paths))
            except Exception as e:
                self.error.emit(str(e))

    class UploadWorker(QObject):
        progress = Signal(str, int, int)      # label, bytes_sent, bytes_total
        finished = Signal(bool, str)

        def __init__(self, items, host, port, user, password,
                     workers: int, verify: bool):
            super().__init__()
            self.items = items
            self.host = host
            self.port = port
            self.user = user
            self.password = password
            self.workers = workers
            self.verify = verify

        def run(self):
            from ftp_client import prepare_upload_plan, upload_many_parallel
            plan = prepare_upload_plan(self.items)
            if not plan:
                self.finished.emit(False, "Nothing to upload.")
                return

            total_bytes = sum(os.path.getsize(l) for l, _ in plan if os.path.exists(l))
            sent_bytes = [0]
            last_emit = [0.0]
            import threading
            lock = threading.Lock()

            def progress_cb(local, remote, sent, total):
                now = time.time()
                with lock:
                    sent_bytes[0] += sent - (progress_cb._last.get(local, 0))
                    progress_cb._last[local] = sent
                    if now - last_emit[0] < 0.1 and sent < total:
                        return
                    last_emit[0] = now
                self.progress.emit(os.path.basename(local),
                                   min(sent_bytes[0], total_bytes), total_bytes)
            progress_cb._last = {}

            ok, fail, errors = upload_many_parallel(
                plan, self.host, self.port, self.user, self.password,
                workers=self.workers, verify=self.verify,
                progress=progress_cb,
            )
            msg = f"Uploaded {ok} file(s)."
            if fail:
                msg += f" {fail} failed."
                for lp, rp, err in errors[:5]:
                    msg += f"\n  {os.path.basename(lp)}: {err}"
            self.finished.emit(fail == 0, msg)

    class TUWorker(QObject):
        progress = Signal(str, int, int)
        finished = Signal(bool, str)

        def __init__(self, items, output_dir):
            super().__init__()
            self.items = items
            self.output_dir = output_dir

        def run(self):
            from xboxunity import XboxUnityClient
            client = XboxUnityClient()
            ok_count = 0
            for _kind, _path, header in self.items:
                if header.media_id == 0:
                    continue
                self.progress.emit(
                    f"Querying TU for {header.display_name or header.title_id_hex}",
                    0, 1)
                tu = client.get_latest_title_update(header.media_id_hex)
                if tu:
                    client.download_title_update(tu, self.output_dir)
                    ok_count += 1
            self.finished.emit(True, f"Downloaded {ok_count} TU(s)")

    class FatxWorker(QObject):
        finished = Signal(list, str)

        def __init__(self, device, offset):
            super().__init__()
            self.device = device
            self.offset = offset

        def run(self):
            from fatx import FatxVolume, FatxError
            try:
                with FatxVolume(self.device, offset=self.offset) as vol:
                    items = []
                    for path, entry in vol.walk():
                        if entry.is_directory:
                            continue
                        from stfs import parse_stfs_header, BLOCK_SIZE
                        try:
                            data = vol.read_file(entry)[:BLOCK_SIZE]
                        except Exception:
                            data = b""
                        header = parse_stfs_header(data) if data else None
                        if header:
                            items.append(("stfs", path, header, entry))
                    self.finished.emit(items, f"Found {len(items)} content file(s)")
            except FatxError as e:
                self.finished.emit([], f"FATX error: {e}")
            except Exception as e:
                self.finished.emit([], f"Unexpected error: {e}")

    # ------------------------------------------------------------------
    # Main Window
    # ------------------------------------------------------------------

    class MainWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Xbox 360 Content Manager v2")
            self.resize(1350, 820)
            self.items = []
            self.settings = QSettings("x360cm", "x360cm")
            self.setAcceptDrops(True)
            self._build_ui()
            self._load_settings()

        # ----- UI construction -----

        def _build_ui(self):
            central = QWidget()
            self.setCentralWidget(central)
            root = QVBoxLayout(central)

            # Menu
            menu = self.menuBar()

            m_file = menu.addMenu("&File")
            self._add_action(m_file, "Add &Files…", self.add_files)
            self._add_action(m_file, "Add F&older…", self.add_folder)
            m_file.addSeparator()
            self._add_action(m_file, "Open &FATX Device…", self.open_fatx)
            m_file.addSeparator()
            self._add_action(m_file, "E&xit", self.close)

            m_tools = menu.addMenu("&Tools")
            self._add_action(m_tools, "&Unlock Selected", self.unlock_selected)
            self._add_action(m_tools, "&Restore Selected from Backup",
                             self.restore_selected)
            self._add_action(m_tools, "&Upload Selected", self.upload_selected)
            m_tools.addSeparator()
            self._add_action(m_tools, "Download &TUs for Selected",
                             self.download_tus)
            m_tools.addSeparator()
            self._add_action(m_tools, "&Test FTP Connection", self.test_ftp)

            m_help = menu.addMenu("&Help")
            self._add_action(m_help, "&About", self.show_about)

            # Toolbar row 1
            tb = QHBoxLayout()
            for label, slot in (("Add Files…", self.add_files),
                                ("Add Folder…", self.add_folder),
                                ("Remove Selected", self.remove_selected),
                                ("Clear", self.clear_all)):
                b = QPushButton(label)
                b.clicked.connect(slot)
                tb.addWidget(b)
            tb.addStretch()
            b = QPushButton("Unlock Selected")
            b.clicked.connect(self.unlock_selected)
            tb.addWidget(b)
            b = QPushButton("Restore Backup")
            b.clicked.connect(self.restore_selected)
            tb.addWidget(b)
            b = QPushButton("Download TUs")
            b.clicked.connect(self.download_tus)
            tb.addWidget(b)
            root.addLayout(tb)

            # Search
            srow = QHBoxLayout()
            srow.addWidget(QLabel("Search:"))
            self.search_box = QLineEdit()
            self.search_box.setPlaceholderText("Filter by name, TitleID, MediaID…")
            self.search_box.textChanged.connect(self._apply_filter)
            srow.addWidget(self.search_box)
            root.addLayout(srow)

            # Splitter
            splitter = QSplitter(Qt.Vertical)

            self.table = QTableWidget(0, 7)
            self.table.setHorizontalHeaderLabels(
                ["Type", "Name", "Title ID", "Media ID",
                 "Content Type", "Status", "Path"])
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.table.setSortingEnabled(True)
            self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
            self.table.horizontalHeader().setStretchLastSection(True)
            for i, w in enumerate((60, 280, 90, 90, 170, 90, 320)):
                self.table.setColumnWidth(i, w)
            self.table.setContextMenuPolicy(Qt.CustomContextMenu)
            self.table.customContextMenuRequested.connect(self._show_context_menu)
            splitter.addWidget(self.table)

            self.log = QTextEdit()
            self.log.setReadOnly(True)
            self.log.setMaximumHeight(170)
            splitter.addWidget(self.log)
            root.addWidget(splitter)

            # FTP group
            grp = QGroupBox("FTP Settings (Aurora / FSD)")
            gl = QHBoxLayout(grp)
            gl.addWidget(QLabel("Host:"))
            self.f_host = QLineEdit("192.168.1.100")
            gl.addWidget(self.f_host)
            gl.addWidget(QLabel("Port:"))
            self.f_port = QSpinBox(); self.f_port.setRange(1, 65535); self.f_port.setValue(21)
            gl.addWidget(self.f_port)
            gl.addWidget(QLabel("User:"))
            self.f_user = QLineEdit("xboxftp")
            gl.addWidget(self.f_user)
            gl.addWidget(QLabel("Pass:"))
            self.f_pass = QLineEdit("xboxftp"); self.f_pass.setEchoMode(QLineEdit.Password)
            gl.addWidget(self.f_pass)
            gl.addWidget(QLabel("Workers:"))
            self.f_workers = QSpinBox(); self.f_workers.setRange(1, 16); self.f_workers.setValue(2)
            gl.addWidget(self.f_workers)
            self.f_verify = QCheckBox("Verify"); self.f_verify.setChecked(True)
            gl.addWidget(self.f_verify)
            root.addWidget(grp)

            # Upload row
            ur = QHBoxLayout()
            self.btn_upload = QPushButton("Upload Selected to Xbox")
            self.btn_upload.setStyleSheet("font-weight: bold; padding: 6px;")
            self.btn_upload.clicked.connect(self.upload_selected)
            ur.addWidget(self.btn_upload)
            b = QPushButton("Test FTP Connection")
            b.clicked.connect(self.test_ftp)
            ur.addWidget(b)
            ur.addStretch()
            self.eta_label = QLabel("")
            ur.addWidget(self.eta_label)
            self.progress = QProgressBar()
            self.progress.setRange(0, 100)
            self.progress.setMinimumWidth(340)
            ur.addWidget(self.progress)
            root.addLayout(ur)

            self.setStatusBar(QStatusBar())
            self.statusBar().showMessage("Ready")

        def _add_action(self, menu, text, slot):
            a = QAction(text, self)
            a.triggered.connect(slot)
            menu.addAction(a)

        # ----- Drag & drop -----

        def dragEnterEvent(self, event: QDragEnterEvent):
            if event.mimeData().hasUrls():
                event.acceptProposedAction()

        def dropEvent(self, event: QDropEvent):
            paths = [u.toLocalFile() for u in event.mimeData().urls()]
            if paths:
                self._start_scan(paths)

        # ----- Settings -----

        def _load_settings(self):
            self.f_host.setText(self.settings.value("ftp/host", "192.168.1.100"))
            self.f_port.setValue(int(self.settings.value("ftp/port", 21)))
            self.f_user.setText(self.settings.value("ftp/user", "xboxftp"))
            self.f_pass.setText(self.settings.value("ftp/pass", "xboxftp"))
            self.f_workers.setValue(int(self.settings.value("ftp/workers", 2)))

        def _save_settings(self):
            self.settings.setValue("ftp/host", self.f_host.text())
            self.settings.setValue("ftp/port", self.f_port.value())
            self.settings.setValue("ftp/user", self.f_user.text())
            self.settings.setValue("ftp/pass", self.f_pass.text())
            self.settings.setValue("ftp/workers", self.f_workers.value())

        def closeEvent(self, ev):
            self._save_settings()
            super().closeEvent(ev)

        # ----- Logging -----

        def log_msg(self, msg):
            self.log.append(msg)

        # ----- Scan -----

        def add_files(self):
            files, _ = QFileDialog.getOpenFileNames(
                self, "Select Xbox 360 content files", "", "All Files (*)")
            if files:
                self._start_scan(files)

        def add_folder(self):
            d = QFileDialog.getExistingDirectory(
                self, "Select folder containing Xbox 360 content")
            if d:
                self._start_scan([d])

        def open_fatx(self):
            path, _ = QFileDialog.getOpenFileName(
                self, "Select FATX device or image", "", "All Files (*)")
            if not path:
                return
            offset, ok = 0, True
            if not __import__("fatx").is_fatx_volume(path, 0):
                text, ok = __import__("PySide6.QtWidgets", fromlist=["QInputDialog"]).QInputDialog.getText(
                    self, "Offset", "FATX partition offset (hex or decimal):", text="0")
                if ok and text.strip():
                    try:
                        offset = int(text.strip(), 0)
                    except ValueError:
                        QMessageBox.warning(self, "Offset", "Invalid offset.")
                        return
            self.statusBar().showMessage("Scanning FATX device…")
            self.log_msg(f"Opening FATX device: {path} @ offset {offset}")
            self.fatx_worker = FatxWorker(path, offset)
            self.fatx_thread = QThread()
            self.fatx_worker.moveToThread(self.fatx_thread)
            self.fatx_thread.started.connect(self.fatx_worker.run)
            self.fatx_worker.finished.connect(self._on_fatx_done)
            self.fatx_worker.finished.connect(self.fatx_thread.quit)
            self.fatx_thread.start()

        def _on_fatx_done(self, items, msg):
            self.log_msg(msg)
            self.statusBar().showMessage(msg)
            # Convert to display format compatible with our table
            for kind, path, header, _entry in items:
                self.items.append((kind, path, header))
            self._refresh()

        def _start_scan(self, paths):
            self.statusBar().showMessage("Scanning…")
            self.progress.setRange(0, 0)
            self.log_msg(f"Scanning {len(paths)} path(s)…")
            self.scan_worker = ScanWorker(paths)
            self.scan_thread = QThread()
            self.scan_worker.moveToThread(self.scan_thread)
            self.scan_thread.started.connect(self.scan_worker.run)
            self.scan_worker.finished.connect(self._on_scan_done)
            self.scan_worker.error.connect(self._on_scan_error)
            self.scan_worker.finished.connect(self.scan_thread.quit)
            self.scan_worker.error.connect(self.scan_thread.quit)
            self.scan_thread.start()

        def _on_scan_done(self, results):
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.items.extend(results)
            self._refresh()
            self.statusBar().showMessage(f"Found {len(results)} new item(s)")
            self.log_msg(f"Scan complete: {len(results)} item(s) found.")

        def _on_scan_error(self, msg):
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            QMessageBox.critical(self, "Scan Error", msg)
            self.log_msg(f"Scan error: {msg}")

        # ----- Table refresh -----

        def _refresh(self):
            self.table.setSortingEnabled(False)
            self.table.setRowCount(len(self.items))
            for row, (kind, path, h) in enumerate(self.items):
                self.table.setItem(row, 0, QTableWidgetItem(kind.upper()))
                self.table.setItem(row, 1, QTableWidgetItem(
                    h.display_name or h.title_name or "Unknown"))
                self.table.setItem(row, 2, QTableWidgetItem(h.title_id_hex))
                self.table.setItem(row, 3, QTableWidgetItem(h.media_id_hex))
                self.table.setItem(row, 4, QTableWidgetItem(h.content_type_name))
                if kind == "stfs":
                    status = "Unlocked" if is_unlocked(path) else "Locked"
                elif kind == "god":
                    status = "Unlocked" if is_god_unlocked(path) else "Locked"
                else:
                    status = "?"
                self.table.setItem(row, 5, QTableWidgetItem(status))
                self.table.setItem(row, 6, QTableWidgetItem(path))
            self.table.setSortingEnabled(True)
            self._apply_filter(self.search_box.text())

        def _apply_filter(self, text):
            text = (text or "").strip().lower()
            for row in range(self.table.rowCount()):
                match = True
                if text:
                    match = False
                    for col in (1, 2, 3, 4, 6):
                        item = self.table.item(row, col)
                        if item and text in item.text().lower():
                            match = True
                            break
                self.table.setRowHidden(row, not match)

        # ----- Selection helpers -----

        def _selected_rows(self):
            return sorted({i.row() for i in self.table.selectedIndexes()})

        def _selected_items(self):
            rows = self._selected_rows()
            return [self.items[r] for r in rows if 0 <= r < len(self.items)]

        def remove_selected(self):
            for row in reversed(self._selected_rows()):
                if 0 <= row < len(self.items):
                    del self.items[row]
            self._refresh()

        def clear_all(self):
            self.items.clear()
            self._refresh()
            self.log_msg("Cleared all items.")

        # ----- Context menu -----

        def _show_context_menu(self, pos):
            menu = QMenu(self)
            menu.addAction("Unlock Selected", self.unlock_selected)
            menu.addAction("Restore from Backup", self.restore_selected)
            menu.addAction("Upload Selected", self.upload_selected)
            menu.addSeparator()
            menu.addAction("Download TUs for Selected", self.download_tus)
            menu.addSeparator()
            menu.addAction("Remove from List", self.remove_selected)
            menu.exec(self.table.viewport().mapToGlobal(pos))

        # ----- Actions -----

        def unlock_selected(self):
            items = self._selected_items()
            if not items:
                QMessageBox.information(self, "Unlock", "No items selected.")
                return
            ok = fail = 0
            for kind, path, h in items:
                name = h.display_name or h.title_name or os.path.basename(path)
                if unlock_item(kind, path, make_backup=True):
                    ok += 1
                    self.log_msg(f"Unlocked: {name}")
                else:
                    fail += 1
                    self.log_msg(f"Failed to unlock: {name}")
            self._refresh()
            QMessageBox.information(
                self, "Unlock Result",
                f"Unlocked {ok} item(s)." + (f" {fail} failed." if fail else ""))

        def restore_selected(self):
            from stfs import restore_backup
            from svod import find_god_header
            items = self._selected_items()
            if not items:
                QMessageBox.information(self, "Restore", "No items selected.")
                return
            ok = fail = 0
            for kind, path, _h in items:
                target = path
                if kind == "god":
                    target = find_god_header(path) or path
                if restore_backup(target):
                    ok += 1
                    self.log_msg(f"Restored: {target}")
                else:
                    fail += 1
            self._refresh()
            QMessageBox.information(
                self, "Restore Result",
                f"Restored {ok}. Skipped {fail} (no backup).")

        # ----- FTP -----

        def test_ftp(self):
            from ftp_client import XboxFTPClient
            c = XboxFTPClient()
            ok = c.connect(self.f_host.text(), self.f_port.value(),
                           self.f_user.text(), self.f_pass.text())
            if ok:
                try:
                    c.ftp.voidcmd("NOOP")
                    QMessageBox.information(self, "FTP", "Connection successful!")
                    self.log_msg("FTP connection successful.")
                except Exception as e:
                    QMessageBox.warning(self, "FTP", f"Connected, NOOP failed: {e}")
            else:
                QMessageBox.critical(self, "FTP", "Connection failed.")
                self.log_msg("FTP connection failed.")
            c.disconnect()

        def upload_selected(self):
            items = self._selected_items()
            if not items:
                QMessageBox.information(self, "Upload", "No items selected.")
                return
            if QMessageBox.question(
                    self, "Confirm Upload",
                    f"Upload {len(items)} item(s) to {self.f_host.text()}?",
                    QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return

            self._save_settings()
            self.progress.setRange(0, 100)
            self.progress.setValue(0)

            self.upload_start_time = time.time()
            self.upload_worker = UploadWorker(
                items, self.f_host.text(), self.f_port.value(),
                self.f_user.text(), self.f_pass.text(),
                workers=self.f_workers.value(),
                verify=self.f_verify.isChecked(),
            )
            self.upload_thread = QThread()
            self.upload_worker.moveToThread(self.upload_thread)
            self.upload_thread.started.connect(self.upload_worker.run)
            self.upload_worker.progress.connect(self._on_upload_progress)
            self.upload_worker.finished.connect(self._on_upload_done)
            self.upload_worker.finished.connect(self.upload_thread.quit)
            self.btn_upload.setEnabled(False)
            self.upload_thread.start()

        def _on_upload_progress(self, label, sent, total):
            if total <= 0:
                return
            pct = int(sent * 100 / total)
            self.progress.setValue(pct)
            elapsed = time.time() - self.upload_start_time
            if sent > 0 and elapsed > 1:
                rate = sent / elapsed
                remaining = (total - sent) / rate if rate > 0 else 0
                self.eta_label.setText(
                    f"{sent//1024//1024}MB / {total//1024//1024}MB · "
                    f"{rate/1024/1024:.1f} MB/s · ETA {int(remaining)}s")
            self.statusBar().showMessage(f"Uploading {label} ({pct}%)")

        def _on_upload_done(self, ok, msg):
            self.btn_upload.setEnabled(True)
            self.progress.setValue(100 if ok else 0)
            self.eta_label.setText("")
            (QMessageBox.information if ok else QMessageBox.critical)(
                self, "Upload", msg)
            self.statusBar().showMessage(msg)
            self.log_msg(msg)

        # ----- TUs -----

        def download_tus(self):
            items = self._selected_items()
            if not items:
                QMessageBox.information(self, "TUs", "No items selected.")
                return
            out_dir = QFileDialog.getExistingDirectory(
                self, "Select folder to save Title Updates")
            if not out_dir:
                return
            self.tu_worker = TUWorker(items, out_dir)
            self.tu_thread = QThread()
            self.tu_worker.moveToThread(self.tu_thread)
            self.tu_thread.started.connect(self.tu_worker.run)
            self.tu_worker.progress.connect(
                lambda msg, _c, _t: self.statusBar().showMessage(msg))
            self.tu_worker.finished.connect(self._on_tu_done)
            self.tu_worker.finished.connect(self.tu_thread.quit)
            self.tu_thread.start()

        def _on_tu_done(self, ok, msg):
            QMessageBox.information(self, "Title Updates", msg)
            self.log_msg(msg)

        def show_about(self):
            QMessageBox.about(
                self, "About",
                "<h3>Xbox 360 Content Manager v2</h3>"
                "<p>STFS + SVOD parsing, XBLA/DLC unlock with backup, "
                "parallel FTP to Aurora, XboxUnity TU downloads, and "
                "direct FATX device reading.</p>")

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="xbox360_manager",
        description="Xbox 360 Content Manager v2 (GUI + CLI)")
    sub = parser.add_subparsers(dest="command")

    p_scan = sub.add_parser("scan", help="Scan files/folders for content")
    p_scan.add_argument("paths", nargs="+")

    p_unlock = sub.add_parser("unlock", help="Unlock XBLA/DLC content")
    p_unlock.add_argument("paths", nargs="+")
    p_unlock.add_argument("--no-backup", action="store_true",
                          help="Skip creating .bak sidecars")

    p_restore = sub.add_parser("restore",
                               help="Restore files from .x360cm.bak sidecars")
    p_restore.add_argument("paths", nargs="+")

    p_up = sub.add_parser("upload", help="Upload content to Xbox via FTP")
    p_up.add_argument("paths", nargs="+")
    p_up.add_argument("--host", required=True)
    p_up.add_argument("--port", type=int, default=21)
    p_up.add_argument("--user", default="xboxftp")
    p_up.add_argument("--password", default="xboxftp")
    p_up.add_argument("--workers", type=int, default=1)
    p_up.add_argument("--no-verify", action="store_true")

    p_tu_dl = sub.add_parser("tu-download", help="Download a Title Update")
    p_tu_dl.add_argument("media_id")
    p_tu_dl.add_argument("--out", default="./title_updates")

    p_tu_up = sub.add_parser("tu-upload", help="Download and upload a Title Update")
    p_tu_up.add_argument("media_id")
    p_tu_up.add_argument("--title-id", required=True)
    p_tu_up.add_argument("--host", required=True)
    p_tu_up.add_argument("--port", type=int, default=21)
    p_tu_up.add_argument("--user", default="xboxftp")
    p_tu_up.add_argument("--password", default="xboxftp")
    p_tu_up.add_argument("--out", default="./title_updates")

    p_fx = sub.add_parser("fatx-list", help="List files on a FATX device/image")
    p_fx.add_argument("device")
    p_fx.add_argument("--offset", type=lambda s: int(s, 0), default=0)

    p_fe = sub.add_parser("fatx-extract",
                          help="Extract matching files from a FATX device")
    p_fe.add_argument("device")
    p_fe.add_argument("--offset", type=lambda s: int(s, 0), default=0)
    p_fe.add_argument("--filter", default="")
    p_fe.add_argument("--out", default="./fatx_out")

    args = parser.parse_args()
    if args.command is None:
        gui_main()
    else:
        sys.exit(cli_main(args))


if __name__ == "__main__":
    main()
