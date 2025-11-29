import html
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Callable

from PyQt6 import QtCore, QtGui, QtWidgets, QtNetwork
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PyQt6.QtWebEngineWidgets import QWebEngineView

try:
    import qbittorrentapi
except ImportError as exc:  # pragma: no cover - informs user about missing deps
    raise SystemExit(
        "缺少依赖 qbittorrent-api/PyQt6，运行前请执行 `pip install qbittorrent-api PyQt6 PyQt6-WebEngine`"
    ) from exc


CONFIG_PATH = Path("config.json")
PROFILE_PATH = Path("web_profile")

DEFAULT_CONFIG = {
    "qbittorrent": {
        "host": "http://127.0.0.1",
        "port": 8080,
        "username": "admin",
        "password": "adminadmin",
        "verify_ssl": False,
    },
    "web_modes": [
        {
            "name": "KamePT",
            "pattern": r"https?://kamept\.com/details\.php\?id=\d+",
            "template": "{value}",
            "description": "注释里直接放完整的KamePT详情链接",
            "cookie": "",
        },
        {
            "name": "M-Team",
            "pattern": r"(?P<tid>\d{3,})",
            "template": "https://kp.m-team.cc/detail/{tid}",
            "description": "注释里只填数字ID，自动拼接M-Team详情页",
            "cookie": "",
        },
    ],
    "ui": {
        "refresh_interval_sec": 0,
        "require_category_selection": False,
        "auto_scale_web": False,
        "shortcut_up": "W",
        "shortcut_down": "S",
        "shortcut_copy": "D",
    },
    "active_web_mode": None,
}


def ensure_config_file(path: Path) -> Dict:
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_config_file(path: Path, data: Dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass
class WebMode:
    name: str
    pattern: str
    template: str
    description: str = ""
    cookie: str = ""
    categories: Optional[List[str]] = None

    def __post_init__(self) -> None:
        self._regex = re.compile(self.pattern)
        if self.categories is None:
            self.categories = []

    def resolve(self, text: str) -> Optional[str]:
        if not text:
            return None
        match = self._regex.search(text.strip())
        if not match:
            return None
        context = {"value": match.group(0)}
        context.update({k: v for k, v in match.groupdict().items() if v})
        template = self.template or "{value}"
        try:
            return template.format(**context)
        except KeyError:
            return None


@dataclass
class TorrentRecord:
    hash: str
    name: str
    category: str
    state: str
    progress: float
    ratio: float
    save_path: str
    content_path: str
    comment: str
    num_seeds: int
    num_leechs: int
    added_on: int


class QbClient:
    def __init__(self, cfg: Dict):
        self.cfg = cfg
        self.client = qbittorrentapi.Client(
            host=cfg["host"],
            port=cfg.get("port", 8080),
            username=cfg.get("username"),
            password=cfg.get("password"),
            VERIFY_WEBUI_CERTIFICATE=cfg.get("verify_ssl", False),
        )

    def fetch_torrents(self, categories: Optional[List[str]] = None) -> List[TorrentRecord]:
        try:
            self.client.auth_log_in()
        except qbittorrentapi.LoginFailed as exc:
            raise RuntimeError(f"无法登录qBittorrent：{exc}") from exc
        except qbittorrentapi.APIConnectionError as exc:
            raise RuntimeError(f"无法连接qBittorrent：{exc}") from exc

        torrents: List[TorrentRecord] = []
        missing_comment_indices: List[int] = []
        source = self._collect_torrents(categories)
        for idx, torrent in enumerate(source):
            comment = getattr(torrent, "comment", "") or ""
            if not comment:
                missing_comment_indices.append(idx)
            torrents.append(
                TorrentRecord(
                    hash=torrent.hash,
                    name=torrent.name,
                    category=torrent.category or "未分类",
                    state=torrent.state,
                    progress=float(torrent.progress),
                    ratio=float(torrent.ratio),
                    save_path=torrent.save_path,
                    content_path=getattr(torrent, "content_path", torrent.save_path),
                    comment=comment,
                    num_seeds=getattr(torrent, "num_seeds", 0),
                    num_leechs=getattr(torrent, "num_leechs", 0),
                    added_on=getattr(torrent, "added_on", 0),
                )
            )

        for idx in missing_comment_indices:
            torrent_hash = source[idx].hash
            try:
                props = self.client.torrents_properties(torrent_hash)
            except qbittorrentapi.NotFound404Error:
                continue
            except qbittorrentapi.APIConnectionError:
                break
            comment = getattr(props, "comment", "")
            if comment:
                torrents[idx].comment = comment

        return torrents

    def _collect_torrents(self, categories: Optional[List[str]]) -> List[Any]:
        torrents: List[Any] = []
        seen: set[str] = set()
        if not categories:
            torrents = list(self.client.torrents_info())
        else:
            for category in categories:
                try:
                    subset = self.client.torrents_info(category=category)
                except qbittorrentapi.NotFound404Error:
                    continue
                for torrent in subset:
                    if torrent.hash in seen:
                        continue
                    torrents.append(torrent)
                    seen.add(torrent.hash)
        return torrents

    def list_categories(self) -> List[str]:
        try:
            self.client.auth_log_in()
        except qbittorrentapi.LoginFailed as exc:
            raise RuntimeError(f"无法登录qBittorrent：{exc}") from exc
        except qbittorrentapi.APIConnectionError as exc:
            raise RuntimeError(f"无法连接qBittorrent：{exc}") from exc

        try:
            categories = self.client.torrents_categories()
        except qbittorrentapi.APIConnectionError as exc:
            raise RuntimeError(f"获取分类失败：{exc}") from exc
        names = sorted(categories.keys(), key=str.lower) if categories else []
        return names


class FetchThread(QtCore.QThread):
    data_ready = QtCore.pyqtSignal(list)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, client: QbClient, categories: Optional[List[str]] = None):
        super().__init__()
        self.client = client
        self.categories = categories

    def run(self) -> None:
        try:
            data = self.client.fetch_torrents(self.categories)
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.data_ready.emit(data)


class AutoScaleWebView(QWebEngineView):
    resized = QtCore.pyqtSignal(int)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self.resized.emit(self.width())


class ElideLabel(QtWidgets.QLabel):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self._full_text = "-"
        self.setText("-")
        self.setToolTip("-")
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)

    def set_full_text(self, text: str) -> None:
        self._full_text = text or "-"
        self._update_elide()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_elide()

    def _update_elide(self) -> None:
        metrics = self.fontMetrics()
        available = max(10, self.width() - 6)
        elided = metrics.elidedText(self._full_text, Qt.TextElideMode.ElideMiddle, available)
        super().setText(elided)
        self.setToolTip(self._full_text)


class CopyableLabel(ElideLabel):
    copied = QtCore.pyqtSignal(str)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            clipboard = QtWidgets.QApplication.clipboard()
            clipboard.setText(self._full_text)
            self.copied.emit(self._full_text)
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "已复制")
        super().mouseReleaseEvent(event)

    def full_text(self) -> str:
        return self._full_text


class CopyablePlainText(QtWidgets.QPlainTextEdit):
    copied = QtCore.pyqtSignal(str)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumHeight(180)
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(
            """
            QPlainTextEdit {
                border-radius: 6px;
                border: 1px solid #dcdcdc;
                background-color: #fff;
                padding: 4px;
                font-family: Consolas, "Courier New", monospace;
            }
            """
        )

    def set_full_text(self, text: str) -> None:
        self.setPlainText(text or "-")

    def full_text(self) -> str:
        return self.toPlainText()


class InfoPanel(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.labels: Dict[str, QtWidgets.QWidget] = {}
        wrapper = QtWidgets.QVBoxLayout(self)
        wrapper.setContentsMargins(0,0,0,0)
        wrapper.setSpacing(0)
        wrapper.addStretch()
        card = QtWidgets.QFrame()
        card.setObjectName("infoCard")
        card.setStyleSheet(
            """
            QFrame#infoCard {
                background-color: #f5f5f5;
                border-radius: 10px;
                border: 1px solid #e0e0e0;
            }
            QFormLayout > QLabel {
                # font-weight: 600;
                color: #555;
            }
            """
        )
        form = QtWidgets.QFormLayout(card)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(4)
        wrapper.addWidget(card)

        # fields = [
        #     ("分类: ", "category", ElideLabel),
        #     ("状态: ", "state", ElideLabel),
        #     ("主文件", "content_path", CopyablePlainText),
        # ]

        # for label, key, widget_cls in fields:
        #     row_widget = QtWidgets.QWidget()
        #     row_layout = QtWidgets.QHBoxLayout(row_widget)
        #     row_layout.setContentsMargins(0, 0, 0, 0)
        #     row_layout.setSpacing(4)
        #     value_label = widget_cls()
        #     row_layout.addWidget(value_label)
        #     if key == "content_path":
        #         copy_btn = QtWidgets.QPushButton("复制")
        #         copy_btn.setObjectName("copyButton")
        #         copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        #         copy_btn.setFixedWidth(52)
        #         copy_btn.clicked.connect(lambda _, lbl=value_label: self._copy_label_text(lbl))  # type: ignore[arg-type]
        #         row_layout.addWidget(copy_btn)
        #     form.addRow(label, row_widget)
        #     self.labels[key] = value_label

        fields = [
            ("分类: ", "category", ElideLabel),
            ("状态: ", "state", ElideLabel),
            ("保存文件：", "content_path", CopyablePlainText),
        ]

        for label, key, widget_cls in fields:
            row_widget = QtWidgets.QWidget()

            if key == "content_path":
                # 这个字段右侧用垂直布局，让按钮换行显示
                row_layout = QtWidgets.QVBoxLayout(row_widget)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(2)

                value_label = widget_cls()
                row_layout.addWidget(value_label)  # 上面一行显示文本


                # 复制按钮
                copy_btn = QtWidgets.QPushButton("复制")
                copy_btn.setObjectName("copyButton")
                copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                copy_btn.setFixedWidth(52)
                copy_btn.clicked.connect(lambda _, lbl=value_label: self._copy_label_text(lbl))
                row_layout.addWidget(copy_btn)     # 下一行显示按钮

            else:
                # 其他行保持水平布局
                row_layout = QtWidgets.QHBoxLayout(row_widget)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(4)

                value_label = widget_cls()
                row_layout.addWidget(value_label)

            # 把这一行加入 QFormLayout
            form.addRow(label, row_widget)
            self.labels[key] = value_label        





        self.comment_box = QtWidgets.QPlainTextEdit()
        self.comment_box.setReadOnly(True)
        self.comment_box.setFixedHeight(110)
        self.comment_box.setStyleSheet(
            """
            QPlainTextEdit {
                border-radius: 8px;
                background-color: #fff;
                border: 1px solid #e0e0e0;
                padding: 6px;
            }
            """
        )
        self.comment_container = QtWidgets.QWidget()
        comment_layout = QtWidgets.QVBoxLayout(self.comment_container)
        comment_layout.setContentsMargins(0, 0, 0, 0)
        comment_layout.setSpacing(4)
        eye_layout = QtWidgets.QHBoxLayout()
        eye_layout.setContentsMargins(0, 0, 0, 0)
        eye_layout.setSpacing(4)
        eye_label = QtWidgets.QLabel("注释")
        self.toggle_comment_button = QtWidgets.QToolButton()
        self.toggle_comment_button.setCheckable(True)
        self.toggle_comment_button.setChecked(False)
        self.toggle_comment_button.setText("👁️")
        self.toggle_comment_button.setToolTip("点击显示/隐藏注释")
        self.toggle_comment_button.toggled.connect(self._toggle_comment_visibility)
        eye_layout.addWidget(eye_label)
        eye_layout.addWidget(self.toggle_comment_button)
        eye_layout.addStretch()
        comment_layout.addLayout(eye_layout)
        comment_layout.addWidget(self.comment_box)
        self.comment_box.setVisible(False)
        form.addRow(self.comment_container)

        self.open_button = QtWidgets.QPushButton("打开保存路径：")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open_path)  # type: ignore[attr-defined]
        wrapper.addWidget(self.open_button)
        self._current_path: Optional[str] = None
        copy_label = self.labels["content_path"]
        if isinstance(copy_label, CopyableLabel):
            copy_label.setCursor(Qt.CursorShape.PointingHandCursor)

    def update_info(self, record: Optional[TorrentRecord]) -> None:
        if record is None:
            for lbl in self.labels.values():
                lbl.set_full_text("-")
            self.open_button.setEnabled(False)
            self._current_path = None
            self.comment_box.setPlainText("-")
            self.comment_box.setVisible(self.toggle_comment_button.isChecked())
            return

        self.labels["category"].set_full_text(record.category)
        self.labels["state"].set_full_text(record.state)
        self.labels["content_path"].set_full_text(record.content_path)
        self.comment_box.setPlainText(record.comment or "-")
        self.comment_box.setVisible(self.toggle_comment_button.isChecked())
        self._current_path = record.content_path if os.path.exists(record.content_path) else record.save_path
        self.open_button.setEnabled(bool(self._current_path and os.path.exists(self._current_path)))

    def _copy_label_text(self, label: ElideLabel) -> None:
        if hasattr(label, "full_text"):
            text = label.full_text()  # type: ignore[call-arg]
        elif hasattr(label, "text"):
            text = label.text()  # type: ignore[call-arg]
        else:
            text = ""
        clipboard = QtWidgets.QApplication.clipboard()
        clipboard.setText(text)
        QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "已复制")

    def _open_path(self) -> None:
        if not self._current_path:
            return
        path = Path(self._current_path)
        if not path.exists():
            QtWidgets.QMessageBox.warning(self, "路径不存在", f"{path} 不存在")
            return
        if path.is_file():
            os.startfile(path.parent)  # type: ignore[attr-defined]
        else:
            os.startfile(path)  # type: ignore[attr-defined]

    def _toggle_comment_visibility(self, checked: bool) -> None:
        self.comment_box.setVisible(checked)


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, config: Dict, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(820, 520)
        self._config = json.loads(json.dumps(config))
        self._config.setdefault("ui", dict(DEFAULT_CONFIG["ui"]))
        self._modes: List[Dict[str, Any]] = [dict(mode) for mode in self._config.get("web_modes", [])]
        self._last_mode_index: int = -1

        layout = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs)

        self._init_qb_tab()
        self._init_modes_tab()
        self._init_ui_tab()

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _init_qb_tab(self) -> None:
        tab = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(tab)
        qb_cfg = self._config.get("qbittorrent", {})

        self.host_edit = QtWidgets.QLineEdit(qb_cfg.get("host", "http://127.0.0.1"))
        self.port_spin = QtWidgets.QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(int(qb_cfg.get("port", 8080)))
        self.username_edit = QtWidgets.QLineEdit(qb_cfg.get("username", ""))
        self.password_edit = QtWidgets.QLineEdit(qb_cfg.get("password", ""))
        self.password_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.verify_checkbox = QtWidgets.QCheckBox("验证SSL证书")
        self.verify_checkbox.setChecked(bool(qb_cfg.get("verify_ssl", False)))

        form.addRow("Host", self.host_edit)
        form.addRow("Port", self.port_spin)
        form.addRow("用户名", self.username_edit)
        form.addRow("密码", self.password_edit)
        form.addRow("", self.verify_checkbox)

        self.tabs.addTab(tab, "qBittorrent")

    def _init_modes_tab(self) -> None:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(tab)

        left_layout = QtWidgets.QVBoxLayout()
        self.mode_list = QtWidgets.QListWidget()
        self.mode_list.currentRowChanged.connect(self._on_mode_selected)
        left_layout.addWidget(self.mode_list, 1)

        btn_layout = QtWidgets.QHBoxLayout()
        add_btn = QtWidgets.QPushButton("新增")
        add_btn.clicked.connect(self._add_mode)
        remove_btn = QtWidgets.QPushButton("删除")
        remove_btn.clicked.connect(self._remove_mode)
        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(remove_btn)
        left_layout.addLayout(btn_layout)

        left_layout.addWidget(QtWidgets.QLabel("默认匹配模式："))
        self.default_mode_combo = QtWidgets.QComboBox()
        left_layout.addWidget(self.default_mode_combo)

        layout.addLayout(left_layout, 1)

        form_layout = QtWidgets.QFormLayout()
        self.name_edit = QtWidgets.QLineEdit()
        self.pattern_edit = QtWidgets.QLineEdit()
        self.template_edit = QtWidgets.QLineEdit()
        self.desc_edit = QtWidgets.QTextEdit()
        self.desc_edit.setFixedHeight(120)
        self.cookie_edit = QtWidgets.QPlainTextEdit()
        self.cookie_edit.setPlaceholderText("例：uid=xxx; passkey=yyy")
        self.cookie_edit.setFixedHeight(70)

        form_layout.addRow("名称", self.name_edit)
        form_layout.addRow("正则模式", self.pattern_edit)
        form_layout.addRow("URL模板", self.template_edit)
        form_layout.addRow("描述", self.desc_edit)
        form_layout.addRow("Cookie", self.cookie_edit)
        layout.addLayout(form_layout, 2)

        self.tabs.addTab(tab, "网页模式")
        self._reload_mode_list()
        self._refresh_default_mode_combo()

    def _reload_mode_list(self) -> None:
        self.mode_list.blockSignals(True)
        self.mode_list.clear()
        for mode in self._modes:
            self.mode_list.addItem(mode.get("name") or "未命名")
        self.mode_list.blockSignals(False)
        if self.mode_list.count():
            self.mode_list.setCurrentRow(0)
            self._last_mode_index = 0
        else:
            self._load_mode_into_form(None)
            self._last_mode_index = -1

    def _refresh_default_mode_combo(self) -> None:
        current = self._config.get("active_web_mode")
        self.default_mode_combo.blockSignals(True)
        self.default_mode_combo.clear()
        self.default_mode_combo.addItem("自动匹配", None)
        for mode in self._modes:
            self.default_mode_combo.addItem(mode.get("name") or "未命名", mode.get("name"))
        index = self.default_mode_combo.findData(current) if current else 0
        if index < 0:
            index = 0
        self.default_mode_combo.setCurrentIndex(index)
        self.default_mode_combo.blockSignals(False)

    def _on_mode_selected(self, row: int) -> None:
        if row == self._last_mode_index:
            return
        self._apply_current_mode_changes()
        self._last_mode_index = row
        mode = None
        if 0 <= row < len(self._modes):
            mode = self._modes[row]
        self._load_mode_into_form(mode)

    def _apply_current_mode_changes(self) -> None:
        idx = self._last_mode_index
        if idx < 0 or idx >= len(self._modes):
            return
        mode = self._modes[idx]
        mode["name"] = self.name_edit.text().strip() or "未命名"
        mode["pattern"] = self.pattern_edit.text().strip()
        mode["template"] = self.template_edit.text().strip() or "{value}"
        mode["description"] = self.desc_edit.toPlainText().strip()
        mode["cookie"] = self.cookie_edit.toPlainText().strip()
        self.mode_list.item(idx).setText(mode["name"])

    def _load_mode_into_form(self, mode: Optional[Dict[str, Any]]) -> None:
        block = self.blockSignals(True)
        if mode:
            self.name_edit.setText(mode.get("name", ""))
            self.pattern_edit.setText(mode.get("pattern", ""))
            self.template_edit.setText(mode.get("template", ""))
            self.desc_edit.setPlainText(mode.get("description", ""))
            self.cookie_edit.setPlainText(mode.get("cookie", ""))
        else:
            self.name_edit.clear()
            self.pattern_edit.clear()
            self.template_edit.clear()
            self.desc_edit.clear()
            self.cookie_edit.clear()
        self.blockSignals(block)

    def _add_mode(self) -> None:
        self._apply_current_mode_changes()
        new_mode = {
            "name": f"新模式 {len(self._modes) + 1}",
            "pattern": "",
            "template": "{value}",
            "description": "",
            "cookie": "",
        }
        self._modes.append(new_mode)
        self._reload_mode_list()
        if self.mode_list.count():
            self.mode_list.setCurrentRow(self.mode_list.count() - 1)
            self._last_mode_index = self.mode_list.currentRow()
        self._refresh_default_mode_combo()

    def _remove_mode(self) -> None:
        row = self.mode_list.currentRow()
        if row < 0 or row >= len(self._modes):
            return
        del self._modes[row]
        self._last_mode_index = -1
        self._reload_mode_list()
        self._refresh_default_mode_combo()

    def accept(self) -> None:
        self._apply_current_mode_changes()
        if not self._validate_modes():
            return
        self._config["qbittorrent"] = {
            "host": self.host_edit.text().strip() or "http://127.0.0.1",
            "port": self.port_spin.value(),
            "username": self.username_edit.text().strip(),
            "password": self.password_edit.text(),
            "verify_ssl": self.verify_checkbox.isChecked(),
        }
        self._config["web_modes"] = self._modes
        self._config["active_web_mode"] = self.default_mode_combo.currentData()
        ui_cfg = dict(self._config.get("ui", {}))
        ui_cfg["require_category_selection"] = self.require_category_checkbox.isChecked()
        ui_cfg["auto_scale_web"] = self.auto_scale_checkbox.isChecked()
        up_seq = self.up_shortcut_edit.keySequence()
        down_seq = self.down_shortcut_edit.keySequence()
        copy_seq = self.copy_shortcut_edit.keySequence()
        ui_cfg["shortcut_up"] = up_seq.toString(QtGui.QKeySequence.SequenceFormat.PortableText) or "W"
        ui_cfg["shortcut_down"] = down_seq.toString(QtGui.QKeySequence.SequenceFormat.PortableText) or "S"
        ui_cfg["shortcut_copy"] = copy_seq.toString(QtGui.QKeySequence.SequenceFormat.PortableText) or "D"
        self._config["ui"] = ui_cfg
        super().accept()

    def _init_ui_tab(self) -> None:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        ui_cfg = self._config.get("ui", {})

        self.require_category_checkbox = QtWidgets.QCheckBox("未选择分类时不加载种子列表")
        self.require_category_checkbox.setChecked(ui_cfg.get("require_category_selection", False))
        self.auto_scale_checkbox = QtWidgets.QCheckBox("根据窗口宽度自动缩放网页（最多100%）")
        self.auto_scale_checkbox.setChecked(ui_cfg.get("auto_scale_web", False))
        self.up_shortcut_edit = QtWidgets.QKeySequenceEdit(QtGui.QKeySequence(ui_cfg.get("shortcut_up", "W")))
        self.down_shortcut_edit = QtWidgets.QKeySequenceEdit(QtGui.QKeySequence(ui_cfg.get("shortcut_down", "S")))
        self.copy_shortcut_edit = QtWidgets.QKeySequenceEdit(QtGui.QKeySequence(ui_cfg.get("shortcut_copy", "D")))
        self.up_shortcut_edit.setClearButtonEnabled(True)
        self.down_shortcut_edit.setClearButtonEnabled(True)
        self.copy_shortcut_edit.setClearButtonEnabled(True)

        layout.addWidget(self.require_category_checkbox)
        layout.addWidget(self.auto_scale_checkbox)
        layout.addWidget(QtWidgets.QLabel("树列表向上一项快捷键："))
        layout.addWidget(self.up_shortcut_edit)
        layout.addWidget(QtWidgets.QLabel("树列表向下一项快捷键："))
        layout.addWidget(self.down_shortcut_edit)
        layout.addWidget(QtWidgets.QLabel("复制保存文件路径快捷键："))
        layout.addWidget(self.copy_shortcut_edit)
        layout.addStretch()
        self.tabs.addTab(tab, "界面")

    def _validate_modes(self) -> bool:
        for mode in self._modes:
            if not mode.get("pattern"):
                QtWidgets.QMessageBox.warning(self, "校验失败", f"模式 {mode.get('name')} 缺少正则表达式")
                return False
            try:
                re.compile(mode["pattern"])
            except re.error as exc:
                QtWidgets.QMessageBox.warning(
                    self,
                    "校验失败",
                    f"模式 {mode.get('name')} 的正则无效：{exc}",
                )
                return False
        return True

    def get_config(self) -> Dict:
        return self._config


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, config: Dict):
        super().__init__()
        self.setWindowTitle("qbLook - 分类做种状态面板")
        self._base_title = self.windowTitle()
        self.resize(1400, 800)

        self.config = config
        self.web_modes = [WebMode(**mode) for mode in config.get("web_modes", [])]
        self.active_mode_name: Optional[str] = self.config.get("active_web_mode")
        ui_cfg = self.config.get("ui", {})
        self.require_category_selection = bool(ui_cfg.get("require_category_selection", False))
        self.auto_scale_web = bool(ui_cfg.get("auto_scale_web", False))
        self.web_profile = self._create_web_profile()
        self.qb_client = QbClient(config["qbittorrent"])
        self.current_records: Dict[str, TorrentRecord] = {}
        self.fetch_thread: Optional[FetchThread] = None
        self.available_categories: List[str] = []
        self.selected_category: Optional[str] = None
        self._all_categories_value = "__ALL__"
        self._selection_marker = "▶ "
        self._current_tree_item: Optional[QtWidgets.QTreeWidgetItem] = None

        self._setup_ui()
        self._create_actions()
        self.connection_label = QtWidgets.QLabel("qBittorrent：未连接")
        self.statusBar().addPermanentWidget(self.connection_label)
        categories_loaded = self._load_categories()
        self.statusBar().showMessage("准备就绪")
        if categories_loaded and not self.require_category_selection:
            self.selected_category = self._all_categories_value
        self.refresh_data()

    def _setup_ui(self) -> None:
        splitter = QtWidgets.QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["名称"])
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)

        self.web_view = AutoScaleWebView()
        self.web_page = QWebEnginePage(self.web_profile, self.web_view)
        self.web_view.setPage(self.web_page)
        self.web_page.loadFinished.connect(lambda _: self._schedule_web_scaling())
        self.web_view.resized.connect(self._apply_web_scaling_from_signal)
        # self.web_view.setHtml("<h2 style='text-align:center;'>请选择一个种子以加载注释页面</h2>")
        self.web_view.setHtml("""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<style>
    body {
        margin: 0;
        height: 100vh;
        display: flex;
        justify-content: center;
        align-items: center;
        font-family: "Microsoft YaHei", sans-serif;
        background: linear-gradient(135deg, #74ebd5, #ACB6E5); /* 渐变背景 */
    }

    .message-box {
        background-color: rgba(255, 255, 255, 0.95); /* 半透明白色卡片 */
        padding: 40px 60px;
        border-radius: 16px;
        box-shadow: 0 12px 24px rgba(0,0,0,0.2);
        text-align: center;
        animation: fadeIn 0.8s ease-in-out;
    }

    .message-box h2 {
        margin: 0;
        font-size: 24px;
        color: #333;
    }

    .message-box p {
        margin-top: 12px;
        color: #666;
        font-size: 16px;
    }

    .icon {
        font-size: 48px;
        color: #4a90e2;
        margin-bottom: 20px;
    }

    /* 简单淡入动画 */
    @keyframes fadeIn {
        from {opacity: 0; transform: translateY(-20px);}
        to {opacity: 1; transform: translateY(0);}
    }
</style>
</head>
<body>
    <div class="message-box">
        <div class="icon">&#128269;</div> <!-- 放大镜图标 -->
        <h2>请选择一个种子以加载注释页面</h2>
        <p>选中左侧种子列表中的一项，即可查看详细信息</p>
    </div>
</body>
</html>
""")
        self._schedule_web_scaling()

        self.info_panel = InfoPanel()

        splitter.addWidget(self.tree)
        splitter.addWidget(self.web_view)
        splitter.addWidget(self.info_panel)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 10)
        splitter.setStretchFactor(2, 5)
        self._setup_shortcuts()

    def _create_actions(self) -> None:
        toolbar = self.addToolBar("主工具栏")
        refresh_action = QtGui.QAction("刷新", self)
        refresh_action.triggered.connect(self.refresh_data)
        toolbar.addAction(refresh_action)

        settings_action = QtGui.QAction("设置", self)
        settings_action.triggered.connect(self.open_settings)
        toolbar.addAction(settings_action)

        toolbar.addSeparator()
        toolbar.addWidget(QtWidgets.QLabel("网页模式："))
        self.mode_selector = QtWidgets.QComboBox()
        self.mode_selector.currentIndexChanged.connect(self._on_mode_selector_changed)
        toolbar.addWidget(self.mode_selector)
        self._refresh_mode_selector()

        # toolbar.addWidget(QtWidgets.QLabel("分类："))
        # self.category_selector = QtWidgets.QComboBox()
        # self.category_selector.currentIndexChanged.connect(self._on_category_selector_changed)
        # toolbar.addWidget(self.category_selector)
        # self._refresh_category_selector()

        toolbar.addWidget(QtWidgets.QLabel("分类："))
        self.category_selector = QtWidgets.QComboBox()
        self.category_selector.currentIndexChanged.connect(self._on_category_selector_changed)
        toolbar.addWidget(self.category_selector)
        self._refresh_category_selector()

    def _load_categories(self) -> bool:
        try:
            categories = self.qb_client.list_categories()
        except RuntimeError as exc:
            self.connection_label.setText("qBittorrent：连接失败")
            self.statusBar().showMessage(str(exc))
            return False
        else:
            self.connection_label.setText("qBittorrent：已连接")
        if categories != self.available_categories:
            self.available_categories = categories
            self._refresh_category_selector()
        return True

    def refresh_data(self) -> None:
        if self.fetch_thread and self.fetch_thread.isRunning():
            return
        if not self._load_categories():
            return
        if self._should_block_fetch():
            self.statusBar().showMessage("请选择分类后再加载数据")
            self.tree.clear()
            self.info_panel.update_info(None)
            return
        self.statusBar().showMessage("正在从 qBittorrent 拉取数据...")
        categories = self._get_selected_categories()
        self.fetch_thread = FetchThread(self.qb_client, categories)
        self.fetch_thread.data_ready.connect(self._on_data_ready)
        self.fetch_thread.failed.connect(self._on_data_failed)
        self.fetch_thread.start()

    def _on_data_ready(self, records: List[TorrentRecord]) -> None:
        self.fetch_thread = None
        self.statusBar().showMessage(f"已加载 {len(records)} 个任务")
        self.current_records = {record.hash: record for record in records}
        categories: Dict[str, List[TorrentRecord]] = {}
        for record in records:
            categories.setdefault(record.category or "未分类", []).append(record)
        self.tree.clear()
        self._current_tree_item = None
        for category, torrents in sorted(categories.items()):
            cat_item = QtWidgets.QTreeWidgetItem([category])
            cat_item.setFirstColumnSpanned(True)
            for torrent in sorted(torrents, key=lambda t: t.name.lower()):
                item = QtWidgets.QTreeWidgetItem([torrent.name])
                item.setData(0, Qt.ItemDataRole.UserRole, torrent.hash)
                item.setData(0, Qt.ItemDataRole.UserRole + 1, torrent.name)
                cat_item.addChild(item)
            self.tree.addTopLevelItem(cat_item)
            cat_item.setExpanded(True)
        self.tree.sortItems(0, Qt.SortOrder.AscendingOrder)

    def _on_data_failed(self, message: str) -> None:
        self.fetch_thread = None
        self.statusBar().showMessage("拉取失败")
        QtWidgets.QMessageBox.critical(self, "拉取失败", message)

    def _on_selection_changed(self) -> None:
        item = self.tree.currentItem()
        if self._current_tree_item and self._current_tree_item is not item:
            self._set_tree_item_marker(self._current_tree_item, False)
        if not item or not item.parent():
            self.info_panel.update_info(None)
            self._current_tree_item = None
            return
        self._set_tree_item_marker(item, True)
        self._current_tree_item = item
        torrent_hash = item.data(0, Qt.ItemDataRole.UserRole)
        record = self.current_records.get(torrent_hash)
        self.info_panel.update_info(record)
        self._update_window_title(record)
        self._update_web_view(record)

    def _update_web_view(self, record: Optional[TorrentRecord]) -> None:
        if record is None:
            self.web_view.setHtml("""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<style>
    body {
        margin: 0;
        height: 100vh;
        display: flex;
        justify-content: center;
        align-items: center;
        font-family: "Microsoft YaHei", sans-serif;
        background: linear-gradient(135deg, #74ebd5, #ACB6E5); /* 渐变背景 */
    }

    .message-box {
        background-color: rgba(255, 255, 255, 0.95); /* 半透明白色卡片 */
        padding: 40px 60px;
        border-radius: 16px;
        box-shadow: 0 12px 24px rgba(0,0,0,0.2);
        text-align: center;
        animation: fadeIn 0.8s ease-in-out;
    }

    .message-box h2 {
        margin: 0;
        font-size: 24px;
        color: #333;
    }

    .message-box p {
        margin-top: 12px;
        color: #666;
        font-size: 16px;
    }

    .icon {
        font-size: 48px;
        color: #4a90e2;
        margin-bottom: 20px;
    }

    /* 简单淡入动画 */
    @keyframes fadeIn {
        from {opacity: 0; transform: translateY(-20px);}
        to {opacity: 1; transform: translateY(0);}
    }
</style>
</head>
<body>
    <div class="message-box">
        <div class="icon">&#128269;</div> <!-- 放大镜图标 -->
        <h2>请选择一个种子以加载注释页面</h2>
        <p>选中左侧种子列表中的一项，即可查看详细信息</p>
    </div>
</body>
</html>
""")
            self._update_window_title(None)
            self._schedule_web_scaling()
            return
        url, mode = self._resolve_comment_url(record.comment)
        if url:
            if mode:
                self._apply_mode_cookie(mode, url)
            self.web_view.load(QUrl(url))
            self._schedule_web_scaling()
            self.statusBar().showMessage(f"加载页面：{url}")
        else:
            escaped_comment = html.escape(record.comment) if record.comment else "无"
            html_content = f"""
                <div style='padding:24px;font-size:16px;'>
                    <h2>未匹配到可用的请求模式</h2>
                    <p>当前注释：{escaped_comment}</p>
                    <p>请检查配置文件里的 web_modes 正则规则。</p>
                </div>
            """
            self.web_view.setHtml(html_content)
            self._schedule_web_scaling()

    def _resolve_comment_url(self, comment: str) -> Tuple[Optional[str], Optional[WebMode]]:
        modes = self._get_effective_modes()
        for mode in modes:
            resolved = mode.resolve(comment)
            if resolved:
                return resolved, mode
        return None, None

    def _get_effective_modes(self) -> List[WebMode]:
        if not self.web_modes:
            return []
        if self.active_mode_name:
            primary = [mode for mode in self.web_modes if mode.name == self.active_mode_name]
            if primary:
                others = [mode for mode in self.web_modes if mode.name != self.active_mode_name]
                return primary + others
        return self.web_modes
 
    def _refresh_category_selector(self) -> None:
        if not hasattr(self, "category_selector"):
            return
        block = self.category_selector.blockSignals(True)
        current = self.selected_category
        self.category_selector.clear()
        if self.require_category_selection:
            self.category_selector.addItem("未选择", None)
        self.category_selector.addItem("全部", self._all_categories_value)
        for name in self.available_categories:
            self.category_selector.addItem(name, name)
        if current is not None:
            idx = self.category_selector.findData(current)
            if idx >= 0:
                self.category_selector.setCurrentIndex(idx)
            else:
                self.category_selector.setCurrentIndex(0)
        else:
            self.category_selector.setCurrentIndex(0)
        self.category_selector.blockSignals(False)
        self.selected_category = self.category_selector.currentData()

    def _should_block_fetch(self) -> bool:
        return self.require_category_selection and (self.selected_category is None)

    def _get_selected_categories(self) -> Optional[List[str]]:
        if self.selected_category in (None, self._all_categories_value):
            return None
        return [self.selected_category]

    def _apply_mode_cookie(self, mode: WebMode, url: str) -> None:
        cookie_string = (mode.cookie or "").strip()
        if not cookie_string:
            return
        store = self.web_view.page().profile().cookieStore()
        qurl = QUrl(url)
        for part in cookie_string.split(";"):
            part = part.strip()
            if not part:
                continue
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            cookie = QtNetwork.QNetworkCookie(name.strip().encode("utf-8"), value.strip().encode("utf-8"))
            if not cookie.domain():
                cookie.setDomain(qurl.host())
            store.setCookie(cookie, qurl)

    def _update_window_title(self, record: Optional[TorrentRecord]) -> None:
        if record:
            self.setWindowTitle(f"{self._base_title} - {record.name}")
        else:
            self.setWindowTitle(self._base_title)

    def _refresh_mode_selector(self) -> None:
        if not hasattr(self, "mode_selector"):
            return
        current = self.active_mode_name
        block = self.mode_selector.blockSignals(True)
        self.mode_selector.clear()
        self.mode_selector.addItem("自动匹配", None)
        for mode in self.web_modes:
            self.mode_selector.addItem(mode.name, mode.name)
        if current:
            idx = self.mode_selector.findData(current)
            if idx >= 0:
                self.mode_selector.setCurrentIndex(idx)
            else:
                self.mode_selector.setCurrentIndex(0)
        else:
            self.mode_selector.setCurrentIndex(0)
        self.mode_selector.blockSignals(block)

    def _on_mode_selector_changed(self, index: int) -> None:  # noqa: ARG002
        if not hasattr(self, "mode_selector"):
            return
        self.active_mode_name = self.mode_selector.currentData()
        self.config["active_web_mode"] = self.active_mode_name
        save_config_file(CONFIG_PATH, self.config)
        item = self.tree.currentItem()
        if item and item.parent():
            torrent_hash = item.data(0, Qt.ItemDataRole.UserRole)
            record = self.current_records.get(torrent_hash)
            self._update_web_view(record)

    def _on_category_selector_changed(self, index: int) -> None:  # noqa: ARG002
        if not hasattr(self, "category_selector"):
            return
        self.selected_category = self.category_selector.currentData()
        if self.require_category_selection and self.selected_category is None:
            self.tree.clear()
            self.info_panel.update_info(None)
            self.statusBar().showMessage("请选择分类后再加载数据")
            return
        self.refresh_data()
        self._setup_shortcuts()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.config = dialog.get_config()
            save_config_file(CONFIG_PATH, self.config)
            self._apply_config_changes()

    def _apply_config_changes(self) -> None:
        self.web_modes = [WebMode(**mode) for mode in self.config.get("web_modes", [])]
        self.active_mode_name = self.config.get("active_web_mode")
        self.qb_client = QbClient(self.config["qbittorrent"])
        ui_cfg = self.config.get("ui", {})
        self.require_category_selection = bool(ui_cfg.get("require_category_selection", False))
        self.auto_scale_web = bool(ui_cfg.get("auto_scale_web", False))
        self.available_categories = []
        self.selected_category = None if self.require_category_selection else self._all_categories_value
        self._refresh_mode_selector()
        self._refresh_category_selector()
        self._apply_web_scaling()
        self.refresh_data()
        self._setup_shortcuts()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_web_scaling()

    def _apply_web_scaling_from_signal(self, width: int) -> None:
        self._apply_web_scaling(width)

    def _schedule_web_scaling(self) -> None:
        if not self.auto_scale_web:
            return
        QtCore.QTimer.singleShot(0, self._apply_web_scaling)

    def _apply_web_scaling(self, width: Optional[int] = None) -> None:
        if not hasattr(self, "web_view") or not self.auto_scale_web:
            return
        view_width = width if width is not None else self.web_view.width()
        if view_width <= 0:
            return
        base_width = 1100
        scale_factor = min(1.0, view_width / base_width)
        self.web_view.page().setZoomFactor(scale_factor)
        self._apply_horizontal_scroll_style()

    def _apply_horizontal_scroll_style(self) -> None:
        if not self.auto_scale_web or not hasattr(self, "web_view"):
            return
        script = """
        (function() {
            const styleId = '__qblook_no_horizontal_scroll__';
            let styleEl = document.getElementById(styleId);
            if (!styleEl) {
                styleEl = document.createElement('style');
                styleEl.id = styleId;
                styleEl.textContent = `
                    html, body {
                        overflow-x: hidden !important;
                        max-width: 100%;
                    }
                    * {
                        max-width: 100%;
                        box-sizing: border-box;
                    }
                `;
                document.head.appendChild(styleEl);
            }
        })();
        """
        self.web_view.page().runJavaScript(script)

    def _set_tree_item_marker(self, item: QtWidgets.QTreeWidgetItem, active: bool) -> None:
        original = item.data(0, Qt.ItemDataRole.UserRole + 1) or item.text(0)
        if active:
            item.setText(0, f"{self._selection_marker}{original}")
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
        else:
            item.setText(0, str(original))
            font = item.font(0)
            font.setBold(False)
            item.setFont(0, font)

    def _setup_shortcuts(self) -> None:
        for shortcut in getattr(self, "_shortcuts", []):
            shortcut.setParent(None)
        self._shortcuts = []
        ui_cfg = self.config.get("ui", {})
        up_seq = QtGui.QKeySequence(ui_cfg.get("shortcut_up", "W"))
        down_seq = QtGui.QKeySequence(ui_cfg.get("shortcut_down", "S"))
        copy_seq = QtGui.QKeySequence(ui_cfg.get("shortcut_copy", "D"))
        up_shortcut = QtGui.QShortcut(up_seq, self)
        up_shortcut.activated.connect(self._tree_select_up)  # type: ignore[attr-defined]
        down_shortcut = QtGui.QShortcut(down_seq, self)
        down_shortcut.activated.connect(self._tree_select_down)  # type: ignore[attr-defined]
        copy_shortcut = QtGui.QShortcut(copy_seq, self)
        copy_shortcut.activated.connect(self._copy_current_content_path)  # type: ignore[attr-defined]
        self._shortcuts.extend([up_shortcut, down_shortcut, copy_shortcut])

    def _tree_select_up(self) -> None:
        current = self.tree.currentItem()
        if not current:
            return
        parent = current.parent()
        if not parent:
            return
        index = parent.indexOfChild(current)
        if index > 0:
            self.tree.setCurrentItem(parent.child(index - 1))

    def _tree_select_down(self) -> None:
        current = self.tree.currentItem()
        if not current:
            return
        parent = current.parent()
        if not parent:
            return
        index = parent.indexOfChild(current)
        if index < parent.childCount() - 1:
            self.tree.setCurrentItem(parent.child(index + 1))

    def _copy_current_content_path(self) -> None:
        if not self.info_panel or "content_path" not in self.info_panel.labels:
            return
        label = self.info_panel.labels["content_path"]
        if hasattr(label, "full_text"):
            text = label.full_text()  # type: ignore[call-arg]
        elif hasattr(label, "toPlainText"):
            text = label.toPlainText()  # type: ignore[call-arg]
        else:
            text = getattr(label, "text", lambda: "")()
        if not text:
            return
        QtWidgets.QApplication.clipboard().setText(text)
        self._show_copy_toast("复制成功：保存文件路径已复制")

    def _show_copy_toast(self, message: str) -> None:
        if not hasattr(self, "_toast"):
            self._toast = QtWidgets.QLabel(self)
            self._toast.setStyleSheet(
                """
                QLabel {
                    background-color: rgba(0, 0, 0, 0.8);
                    color: #fff;
                    border-radius: 6px;
                    padding: 6px 12px;
                }
                """
            )
            self._toast.setWindowFlags(
                Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
            )
        self._toast.setText(message)
        self._toast.adjustSize()
        geo = self.geometry()
        x = geo.left() + 20
        y = geo.bottom() - self._toast.height() - 20
        self._toast.move(x, y)
        self._toast.show()
        QtCore.QTimer.singleShot(2000, self._toast.hide)
    
    def _create_web_profile(self) -> QWebEngineProfile:
        PROFILE_PATH.mkdir(parents=True, exist_ok=True)
        storage_path = PROFILE_PATH / "storage"
        cache_path = PROFILE_PATH / "cache"
        storage_path.mkdir(parents=True, exist_ok=True)
        cache_path.mkdir(parents=True, exist_ok=True)

        profile = QWebEngineProfile("qblook_profile", self)
        profile.setPersistentStoragePath(str(storage_path.resolve()))
        profile.setCachePath(str(cache_path.resolve()))
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        return profile


def main() -> None:
    config = ensure_config_file(CONFIG_PATH)
    config.setdefault("ui", dict(DEFAULT_CONFIG["ui"]))
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow(config)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

