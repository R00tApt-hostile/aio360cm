#!/usr/bin/env python3
"""Xbox 360 Content Manager — all-in-one manager for Xbox 360 content.

Usage:
  GUI mode:      python xbox360_manager.py
  CLI scan:      python xbox360_manager.py scan <path> [<path> ...]
  CLI unlock:    python xbox360_manager.py unlock <path> [<path> ...]
  CLI upload:    python xbox360_manager.py upload <path> [<path> ...] --host 192.168.1.x
  CLI tu-download: python xbox360_manager.py tu-download <media_id> --out ./tus
  CLI tu-upload:   python xbox360_manager.py tu-upload <media_id> --host 192.168.1.x --title-id <id>
"""

import argparse
import os
import sys

# ---------------------------------------------------------------------------
# Shared scanning / uploading logic
# ---------------------------------------------------------------------------

def scan_paths(paths):
    """Return a list of (kind, path, StfsHeader) tuples."""
    from stfs import is_stfs_file, read_stfs_header
    from svod import is_god_folder, read_god_header

    results = []
    file_paths = []
    for p in paths:
        if os.path.isfile(p):
            file_paths.append(p)
        elif os.path.isdir(p):
            # Check if the directory itself is a GOD folder
            if is_god_folder(p):
                results.append(("god", p, read_god_header(p)))
                continue
            for root, dirs, names in os.walk(p):
                if is_god_folder(root):
                    results.append(("god", root, read_god_header(root)))
                    dirs[:] = []  # Do not recurse into a GOD game
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


def unlock_item(kind, path):
    from stfs import unlock_stfs
    from svod import unlock_god
    if kind == "stfs":
        return unlock_stfs(path)
    if kind == "god":
        return unlock_god(path)
    return False


def upload_items(items, host, port, user, password,
                 progress=None):
    from ftp_client import XboxFTPClient, build_remote_path

    client = XboxFTPClient()
    if not client.connect(host, port, user, password):
        return False, "FTP connection failed"

    try:
        total = len(items)
        for idx, (kind, path, header) in enumerate(items):
            remote = build_remote_path(header, kind)
            label = header.display_name or os.path.basename(path)
            if progress:
                progress(label, idx + 1, total)

            if kind == "god":
                if not client.upload_folder(path, remote):
                    return False, f"Failed uploading {path}"
            else:
                remote_file = f"{remote}/{os.path.basename(path)}"
                if not client.upload_file(path, remote_file):
                    return False, f"Failed uploading {path}"
        return True, f"Uploaded {total} item(s)"
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
            if unlock_item(kind, path):
                print(f"[OK]   {name}")
                ok += 1
            else:
                print(f"[FAIL] {name}")
                fail += 1
        print(f"\nUnlocked {ok}, failed {fail}.")
        return 0 if fail == 0 else 1

    if args.command == "upload":
        items = scan_paths(args.paths)
        if not items:
            print("No content found.")
            return 1

        def p(label, cur, tot):
            print(f"  [{cur}/{tot}] {label}")

        ok, msg = upload_items(items, args.host, args.port,
                               args.user, args.password, progress=p)
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
        if path:
            print(f"Saved to {path}")
            return 0
        return 1

    if args.command == "tu-upload":
        from xboxunity import XboxUnityClient
        from ftp_client import XboxFTPClient
        client = XboxUnityClient()
        tu = client.get_latest_title_update(args.media_id)
        if not tu:
            print(f"No Title Update found for MediaID {args.media_id}")
            return 1
        tmp_dir = args.out or "."
        path = client.download_title_update(tu, tmp_dir)
        if not path:
            return 1

        ftp = XboxFTPClient()
        if not ftp.connect(args.host, args.port, args.user, args.password):
            print("FTP connection failed.")
            return 1
        remote = f"Content/0000000000000000/{args.title_id}/000B0000/{os.path.basename(path)}"
        ok = ftp.upload_file(path, remote)
        ftp.disconnect()
        print(f"{'Uploaded' if ok else 'Failed to upload'} {remote}")
        return 0 if ok else 1

    return 0


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

def gui_main():
    try:
        from PySide6.QtCore import Qt, QThread, Signal, QObject, QSettings
        from PySide6.QtWidgets import (
            QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
            QPushButton, QLabel, QLineEdit, QTableWidget, QTableWidgetItem,
            QFileDialog, QMessageBox, QProgressBar, QHeaderView, QGroupBox,
            QSpinBox, QStatusBar, QAbstractItemView, QTextEdit, QSplitter,
        )
        from PySide6.QtGui import QAction
    except ImportError:
        print("PySide6 is required for GUI mode. Install with:")
        print("  pip install PySide6")
        sys.exit(1)

    from stfs import is_unlocked
    from svod import is_god_unlocked

    # ---- Workers ----

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
        progress = Signal(str, int, int)
        finished = Signal(bool, str)

        def __init__(self, items, host, port, user, password):
            super().__init__()
            self.items = items
            self.host = host
            self.port = port
            self.user = user
            self.password = password

        def run(self):
            ok, msg = upload_items(
                self.items, self.host, self.port,
                self.user, self.password,
                progress=lambda l, c, t: self.progress.emit(l, c, t),
            )
            self.finished.emit(ok, msg)

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
            for kind, path, header in self.items:
                if header.media_id == 0:
                    continue
                self.progress.emit(
                    f"Querying TU for {header.display_name or header.title_id_hex}",
                    0, 1,
                )
                tu = client.get_latest_title_update(header.media_id_hex)
                if tu:
                    client.download_title_update(
                        tu, self.output_dir,
                        progress=lambda c, t: self.progress.emit(
                            f"Downloading {tu.name}", c, t),
                    )
                    ok_count += 1
            self.finished.emit(True, f"Downloaded {ok_count} TU(s)")

    # ---- Main Window ----

    class MainWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Xbox 360 Content Manager")
            self.resize(1250, 780)
            self.items = []
            self.settings = QSettings("x360cm", "x360cm")
            self._build_ui()
            self._load_settings()

        def _build_ui(self):
            central = QWidget()
            self.setCentralWidget(central)
            root = QVBoxLayout(central)

            # Menu
            menu = self.menuBar()
            file_menu = menu.addMenu("&File")
            act_add_files = QAction("Add &Files…", self)
            act_add_files.triggered.connect(self.add_files)
            file_menu.addAction(act_add_files)
            act_add_folder = QAction("Add F&older…", self)
            act_add_folder.triggered.connect(self.add_folder)
            file_menu.addAction(act_add_folder)
            file_menu.addSeparator()
            act_exit = QAction("E&xit", self)
            act_exit.triggered.connect(self.close)
            file_menu.addAction(act_exit)

            tools_menu = menu.addMenu("&Tools")
            act_unlock = QAction("&Unlock Selected", self)
            act_unlock.triggered.connect(self.unlock_selected)
            tools_menu.addAction(act_unlock)
            act_upload = QAction("&Upload Selected", self)
            act_upload.triggered.connect(self.upload_selected)
            tools_menu.addAction(act_upload)
            tools_menu.addSeparator()
            act_tu = QAction("Download &TUs for Selected", self)
            act_tu.triggered.connect(self.download_tus)
            tools_menu.addAction(act_tu)
            tools_menu.addSeparator()
            act_test = QAction("&Test FTP Connection", self)
            act_test.triggered.connect(self.test_ftp)
            tools_menu.addAction(act_test)

            help_menu = menu.addMenu("&Help")
            act_about = QAction("&About", self)
            act_about.triggered.connect(self.show_about)
            help_menu.addAction(act_about)

            # Toolbar
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
            b = QPushButton("Download TUs")
            b.clicked.connect(self.download_tus)
            tb.addWidget(b)
            root.addLayout(tb)

            # Splitter: table + log
            splitter = QSplitter(Qt.Vertical)

            self.table = QTableWidget(0, 7)
            self.table.setHorizontalHeaderLabels(
                ["Type", "Name", "Title ID", "Media ID",
                 "Content Type", "Status", "Path"])
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
            self.table.horizontalHeader().setStretchLastSection(True)
            for i, w in enumerate((60, 280, 90, 90, 170, 90, 320)):
                self.table.setColumnWidth(i, w)
            splitter.addWidget(self.table)

            self.log = QTextEdit()
            self.log.setReadOnly(True)
            self.log.setMaximumHeight(150)
            splitter.addWidget(self.log)
            root.addWidget(splitter)

            # FTP group
            grp = QGroupBox("FTP Settings (Aurora / FSD)")
            gl = QHBoxLayout(grp)
            gl.addWidget(QLabel("Host:"))
            self.f_host = QLineEdit("192.168.1.100")
            gl.addWidget(self.f_host)
            gl.addWidget(QLabel("Port:"))
            self.f_port = QSpinBox()
            self.f_port.setRange(1, 65535)
            self.f_port.setValue(21)
            gl.addWidget(self.f_port)
            gl.addWidget(QLabel("User:"))
            self.f_user = QLineEdit("xboxftp")
            gl.addWidget(self.f_user)
            gl.addWidget(QLabel("Pass:"))
            self.f_pass = QLineEdit("xboxftp")
            self.f_pass.setEchoMode(QLineEdit.Password)
            gl.addWidget(self.f_pass)
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
            self.progress = QProgressBar()
            self.progress.setRange(0, 100)
            self.progress.setMinimumWidth(320)
            ur.addWidget(self.progress)
            root.addLayout(ur)

            self.setStatusBar(QStatusBar())
            self.statusBar().showMessage("Ready")

        # ---- Settings ----

        def _load_settings(self):
            self.f_host.setText(self.settings.value("ftp/host", "192.168.1.100"))
            self.f_port.setValue(int(self.settings.value("ftp/port", 21)))
            self.f_user.setText(self.settings.value("ftp/user", "xboxftp"))
            self.f_pass.setText(self.settings.value("ftp/pass", "xboxftp"))

        def _save_settings(self):
            self.settings.setValue("ftp/host", self.f_host.text())
            self.settings.setValue("ftp/port", self.f_port.value())
            self.settings.setValue("ftp/user", self.f_user.text())
            self.settings.setValue("ftp/pass", self.f_pass.text())

        def closeEvent(self, ev):
            self._save_settings()
            super().closeEvent(ev)

        # ---- Logging ----

        def log_msg(self, msg):
            self.log.append(msg)

        # ---- Scan ----

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

        def _refresh(self):
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

        # ---- Row actions ----

        def _selected_rows(self):
            return sorted({i.row() for i in self.table.selectedIndexes()})

        def remove_selected(self):
            for row in reversed(self._selected_rows()):
                if 0 <= row < len(self.items):
                    del self.items[row]
            self._refresh()

        def clear_all(self):
            self.items.clear()
            self._refresh()
            self.log_msg("Cleared all items.")

        def unlock_selected(self):
            rows = self._selected_rows()
            if not rows:
                QMessageBox.information(self, "Unlock", "No items selected.")
                return
            ok = fail = 0
            for row in rows:
                kind, path, h = self.items[row]
                name = h.display_name or h.title_name or os.path.basename(path)
                if unlock_item(kind, path):
                    ok += 1
                    self.log_msg(f"Unlocked: {name}")
                else:
                    fail += 1
                    self.log_msg(f"Failed to unlock: {name}")
            self._refresh()
            QMessageBox.information(
                self, "Unlock Result",
                f"Unlocked {ok} item(s)." + (f" {fail} failed." if fail else ""))

        # ---- FTP ----

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
                    self.log_msg(f"FTP NOOP failed: {e}")
            else:
                QMessageBox.critical(self, "FTP", "Connection failed.")
                self.log_msg("FTP connection failed.")
            c.disconnect()

        def upload_selected(self):
            rows = self._selected_rows()
            if not rows:
                QMessageBox.information(self, "Upload", "No items selected.")
                return
            items = [self.items[r] for r in rows]
            if QMessageBox.question(
                    self, "Confirm Upload",
                    f"Upload {len(items)} item(s) to {self.f_host.text()}?",
                    QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return

            self._save_settings()
            self.progress.setRange(0, 100)
            self.progress.setValue(0)

            self.upload_worker = UploadWorker(
                items, self.f_host.text(), self.f_port.value(),
                self.f_user.text(), self.f_pass.text())
            self.upload_thread = QThread()
            self.upload_worker.moveToThread(self.upload_thread)
            self.upload_thread.started.connect(self.upload_worker.run)
            self.upload_worker.progress.connect(self._on_upload_progress)
            self.upload_worker.finished.connect(self._on_upload_done)
            self.upload_worker.finished.connect(self.upload_thread.quit)
            self.btn_upload.setEnabled(False)
            self.upload_thread.start()

        def _on_upload_progress(self, label, cur, tot):
            self.statusBar().showMessage(f"[{cur}/{tot}] {label}")
            if tot:
                self.progress.setValue(int(cur * 100 / tot))

        def _on_upload_done(self, ok, msg):
            self.btn_upload.setEnabled(True)
            self.progress.setValue(100 if ok else 0)
            (QMessageBox.information if ok else QMessageBox.critical)(
                self, "Upload", msg)
            self.statusBar().showMessage(msg)
            self.log_msg(msg)

        # ---- Title Updates ----

        def download_tus(self):
            rows = self._selected_rows()
            if not rows:
                QMessageBox.information(self, "TUs", "No items selected.")
                return
            items = [self.items[r] for r in rows]
            out_dir = QFileDialog.getExistingDirectory(
                self, "Select folder to save Title Updates")
            if not out_dir:
                return

            self.tu_worker = TUWorker(items, out_dir)
            self.tu_thread = QThread()
            self.tu_worker.moveToThread(self.tu_thread)
            self.tu_thread.started.connect(self.tu_worker.run)
            self.tu_worker.progress.connect(
                lambda msg, c, t: self.statusBar().showMessage(msg))
            self.tu_worker.finished.connect(self._on_tu_done)
            self.tu_worker.finished.connect(self.tu_thread.quit)
            self.tu_thread.start()

        def _on_tu_done(self, ok, msg):
            QMessageBox.information(self, "Title Updates", msg)
            self.log_msg(msg)

        def show_about(self):
            QMessageBox.about(
                self, "About",
                "<h3>Xbox 360 Content Manager</h3>"
                "<p>All-in-one manager for STFS and SVOD (GOD) content.</p>"
                "<p>Parses CON/LIVE/PIRS packages, unlocks XBLA/DLC, "
                "downloads Title Updates from XboxUnity, and uploads to "
                "Aurora via FTP.</p>")

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="xbox360_manager",
        description="Xbox 360 Content Manager (GUI + CLI)")
    sub = parser.add_subparsers(dest="command")

    p_scan = sub.add_parser("scan", help="Scan files/folders for content")
    p_scan.add_argument("paths", nargs="+")

    p_unlock = sub.add_parser("unlock", help="Unlock XBLA/DLC content")
    p_unlock.add_argument("paths", nargs="+")

    p_up = sub.add_parser("upload", help="Upload content to Xbox via FTP")
    p_up.add_argument("paths", nargs="+")
    p_up.add_argument("--host", required=True)
    p_up.add_argument("--port", type=int, default=21)
    p_up.add_argument("--user", default="xboxftp")
    p_up.add_argument("--password", default="xboxftp")

    p_tu_dl = sub.add_parser("tu-download", help="Download a Title Update")
    p_tu_dl.add_argument("media_id", help="Game MediaID (hex)")
    p_tu_dl.add_argument("--out", default="./title_updates")

    p_tu_up = sub.add_parser("tu-upload", help="Download and upload a Title Update")
    p_tu_up.add_argument("media_id", help="Game MediaID (hex)")
    p_tu_up.add_argument("--title-id", required=True)
    p_tu_up.add_argument("--host", required=True)
    p_tu_up.add_argument("--port", type=int, default=21)
    p_tu_up.add_argument("--user", default="xboxftp")
    p_tu_up.add_argument("--password", default="xboxftp")
    p_tu_up.add_argument("--out", default="./title_updates")

    args = parser.parse_args()
    if args.command is None:
        gui_main()
    else:
        sys.exit(cli_main(args))


if __name__ == "__main__":
    main()
