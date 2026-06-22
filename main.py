import sys
import os
import threading
import json
import time
import asyncio
import io
import edge_tts
import pygame
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QLineEdit, QComboBox,
                             QSpinBox, QTimeEdit, QPushButton, QListWidget,
                             QMessageBox, QRadioButton, QButtonGroup, QCheckBox,
                             QSystemTrayIcon, QMenu)
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtCore import QTimer, QTime
from plyer import notification
from PyQt6.QtCore import pyqtSignal, QObject

# 音色列表
VOICES = {
    "女声": [
        ("zh-CN-XiaoxiaoNeural", "晓晓 - 温柔 - 女"),
        ("zh-CN-XiaoyiNeural", "晓伊 - 甜美 - 女"),
    ],
    "男声": [
        ("zh-CN-YunxiNeural", "云希 - 清朗 - 男"),
        ("zh-CN-YunyangNeural", "云扬 - 阳光 - 男"),
        ("zh-CN-YunjianNeural", "云健 - 稳重 - 男"),
        ("zh-CN-YunxiaNeural", "云夏 - 热情 - 男"),
    ]
}

# 默认音色
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

CONFIG_FILE = "reminder_tasks.json"

# 默认代理配置
DEFAULT_PROXY = None  # 例如: "http://127.0.0.1:7897"

# 本地语音引擎标识
LOCAL_TTS_VOICE = "local_tts"


class MySignals(QObject):
    trigger = pyqtSignal(str)


class ReminderTask:
    def __init__(self, title, content, remind_type, mode, target_time_str, interval_min, repeat_count,
                 voice=DEFAULT_VOICE, triggered_count=0, is_active=True):
        self.title = title
        self.content = content
        self.remind_type = remind_type
        self.mode = mode
        self.target_time_str = target_time_str
        self.interval_min = interval_min
        self.repeat_count = repeat_count
        self.voice = voice
        self.triggered_count = triggered_count
        self.is_active = is_active
        self.next_trigger_timestamp = time.time() + (self.interval_min * 60)

    def to_dict(self):
        return {
            "title": self.title,
            "content": self.content,
            "remind_type": self.remind_type,
            "mode": self.mode,
            "target_time_str": self.target_time_str,
            "interval_min": self.interval_min,
            "repeat_count": self.repeat_count,
            "voice": self.voice,
            "triggered_count": self.triggered_count,
            "is_active": self.is_active
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            title=d["title"],
            content=d["content"],
            remind_type=d["remind_type"],
            mode=d["mode"],
            target_time_str=d["target_time_str"],
            interval_min=d["interval_min"],
            repeat_count=d["repeat_count"],
            voice=d.get("voice", DEFAULT_VOICE),
            triggered_count=d.get("triggered_count", 0),
            is_active=d.get("is_active", True)
        )

    def __str__(self):
        type_str = f"[{self.remind_type}]"
        if self.mode == "定时提醒":
            mode_str = f"每天 {self.target_time_str}"
        else:
            rep_str = "无限重复" if self.repeat_count == -1 else (
                f"重复 {self.repeat_count} 次" if self.repeat_count > 0 else "一次性")
            mode_str = f"每隔 {self.interval_min} 分钟 ({rep_str})"
        return f"{type_str} {self.title} - {mode_str}"


def get_reg_key():
    if sys.platform == "win32":
        import winreg
        return winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"
    return None, None


class ReminderApp(QMainWindow):
    def __init__(self):
        super().__init__()

        self.voice_widget = None
        self.txt_title = None
        self.txt_content = None
        self.combo_type = None
        self.rad_countdown = None
        self.rad_timing = None
        self.spin_interval = None
        self.combo_repeat = None
        self.spin_repeat_count = None
        self.time_edit = None
        self.chk_startup = None
        self.list_widget = None
        self.mode_group = None
        self.tray_icon = None
        self.btn_delete = None
        self.btn_add = None
        self.btn_test_audio = None  # 新增：试听按钮
        self.timing_widget = None
        self.countdown_widget = None
        self.btn_test_notification = None
        self.combo_close_action = None
        self.combo_voice = None
        self.txt_proxy = None
        self.setWindowTitle("智能语音提醒助手")
        self.resize(700, 480)

        self.icon_path = "app_icon.ico"
        if os.path.exists(self.icon_path):
            self.app_icon = QIcon(self.icon_path)
        else:
            self.app_icon = self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon)

        self.setWindowIcon(self.app_icon)
        self.tasks = []

        self.current_voice = DEFAULT_VOICE
        self.current_proxy = DEFAULT_PROXY
        
        # 本地TTS引擎（仅在选择本地语音时使用）
        self.tts_engine = None

        self.init_ui()
        self.create_tray_icon()
        self.load_tasks_from_json()
        self.check_startup_status()

        self.main_timer = QTimer()
        self.main_timer.timeout.connect(self.check_tasks)
        self.main_timer.start(1000)

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        # 左侧表单
        form_layout = QVBoxLayout()
        form_layout.addWidget(QLabel("提醒标题:"))
        self.txt_title = QLineEdit()
        self.txt_title.setPlaceholderText("例如：开会、喝水")
        form_layout.addWidget(self.txt_title)

        form_layout.addWidget(QLabel("提醒内容:"))
        self.txt_content = QLineEdit()
        self.txt_content.setPlaceholderText("例如：该去接杯温水喝啦")
        form_layout.addWidget(self.txt_content)

        form_layout.addWidget(QLabel("提醒方式:"))
        self.combo_type = QComboBox()
        self.combo_type.addItems(["通知弹窗", "语音朗读提醒"])
        form_layout.addWidget(self.combo_type)

        # 音色选择（放在widget中以便控制显示/隐藏）
        self.voice_widget = QWidget()
        voice_layout = QVBoxLayout(self.voice_widget)
        voice_layout.setContentsMargins(0, 0, 0, 0)
        voice_layout.addWidget(QLabel("选择音色:"))
        self.combo_voice = QComboBox()
        # 添加女声
        for voice_id, name in VOICES["女声"]:
            self.combo_voice.addItem(name, voice_id)
        # 添加分隔符
        self.combo_voice.insertSeparator(len(VOICES["女声"]))
        # 添加男声
        for voice_id, name in VOICES["男声"]:
            self.combo_voice.addItem(name, voice_id)
        # 添加分隔符
        self.combo_voice.insertSeparator(len(VOICES["女声"]) + len(VOICES["男声"]))
        # 添加本地语音引擎选项
        self.combo_voice.addItem("本地语音引擎 (离线)", LOCAL_TTS_VOICE)
        # 设置默认音色
        self.combo_voice.setCurrentIndex(self.combo_voice.findData(DEFAULT_VOICE))
        voice_layout.addWidget(self.combo_voice)
        form_layout.addWidget(self.voice_widget)
        self.voice_widget.hide()

        # 代理配置
        proxy_layout = QHBoxLayout()
        proxy_layout.addWidget(QLabel("代理地址:"))
        self.txt_proxy = QLineEdit()
        self.txt_proxy.setPlaceholderText("如: http://127.0.0.1:7897")
        proxy_layout.addWidget(self.txt_proxy)
        form_layout.addLayout(proxy_layout)

        # ================= 新增：测试当前配置按钮 =================
        self.btn_test_audio = QPushButton("🔊 试听当前语音内容")
        self.btn_test_audio.clicked.connect(self.test_audio_playback)
        form_layout.addWidget(self.btn_test_audio)

        self.btn_test_notification = QPushButton("💬 试看通知弹窗")
        self.btn_test_notification.clicked.connect(self.test_notification)
        form_layout.addWidget(self.btn_test_notification)

        # 根据选择的提醒方式，决定测试按钮是否可用
        self.combo_type.currentIndexChanged.connect(self.toggle_test_btn_visibility)

        form_layout.addWidget(QLabel("时间配置模式:"))
        self.rad_countdown = QRadioButton("倒计时模式 (一次性/间隔重复)")
        self.rad_timing = QRadioButton("定时模式 (每天固定时间)")
        self.rad_countdown.setChecked(True)

        self.mode_group = QButtonGroup()
        self.mode_group.addButton(self.rad_countdown)
        self.mode_group.addButton(self.rad_timing)
        form_layout.addWidget(self.rad_countdown)
        form_layout.addWidget(self.rad_timing)

        self.countdown_widget = QWidget()
        cd_layout = QVBoxLayout(self.countdown_widget)
        cd_layout.setContentsMargins(0, 0, 0, 0)
        cd_layout.addWidget(QLabel("时间间隔 (分钟):"))
        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(1, 1440)
        self.spin_interval.setValue(20)
        cd_layout.addWidget(self.spin_interval)

        cd_layout.addWidget(QLabel("重复配置:"))
        self.combo_repeat = QComboBox()
        self.combo_repeat.addItems(["仅提醒一次", "无限循环重复", "自定义重复次数"])
        cd_layout.addWidget(self.combo_repeat)

        self.spin_repeat_count = QSpinBox()
        self.spin_repeat_count.setRange(1, 99)
        self.spin_repeat_count.setValue(3)
        self.spin_repeat_count.setEnabled(False)
        cd_layout.addWidget(self.spin_repeat_count)
        form_layout.addWidget(self.countdown_widget)

        self.timing_widget = QWidget()
        tm_layout = QVBoxLayout(self.timing_widget)
        tm_layout.setContentsMargins(0, 0, 0, 0)
        tm_layout.addWidget(QLabel("指定每日提醒时间:"))
        self.time_edit = QTimeEdit()
        self.time_edit.setTime(QTime.currentTime().addSecs(60))
        tm_layout.addWidget(self.time_edit)
        form_layout.addWidget(self.timing_widget)
        self.timing_widget.hide()

        self.rad_countdown.toggled.connect(self.switch_mode_ui)
        self.combo_repeat.currentIndexChanged.connect(self.switch_repeat_spin)

        self.btn_add = QPushButton("添加提醒任务")
        self.btn_add.clicked.connect(self.add_task)
        form_layout.addWidget(self.btn_add)
        form_layout.addStretch()

        # 右侧列表
        list_layout = QVBoxLayout()
        self.chk_startup = QCheckBox("允许此软件开机自动启动")
        self.chk_startup.clicked.connect(self.toggle_startup)
        list_layout.addWidget(self.chk_startup)

        # 关闭行为设置
        close_layout = QHBoxLayout()
        close_layout.addWidget(QLabel("关闭窗口时:"))
        self.combo_close_action = QComboBox()
        self.combo_close_action.addItems(["询问我", "最小化到托盘", "直接退出软件"])
        self.combo_close_action.setCurrentIndex(0)  # 默认询问
        close_layout.addWidget(self.combo_close_action)
        list_layout.addLayout(close_layout)
        # 修改关闭行为设置时自动保存
        self.combo_close_action.currentIndexChanged.connect(self.save_tasks_to_json)

        list_layout.addWidget(QLabel("当前后台监视任务列表 (实时自动保存):"))
        self.list_widget = QListWidget()
        list_layout.addWidget(self.list_widget)

        self.btn_delete = QPushButton("删除选中任务")
        self.btn_delete.clicked.connect(self.delete_task)
        list_layout.addWidget(self.btn_delete)

        main_layout.addLayout(form_layout, stretch=2)
        main_layout.addLayout(list_layout, stretch=3)

        # 初始化时根据提醒方式决定试听按钮状态
        self.toggle_test_btn_visibility()

    # ================= 试听核心控制逻辑 =================
    def test_audio_playback(self):
        """试听按钮的回调函数"""
        title = self.txt_title.text().strip()
        content = self.txt_content.text().strip()

        if not title and not content:
            title = "测试提醒"
            content = "这是一条试听测试语音。"

        # 1. 改变按钮状态并禁用
        self.btn_test_audio.setEnabled(False)
        self.btn_test_audio.setText("正在播报...")

        # 2. 获取当前选择的音色
        self.current_voice = self.combo_voice.currentData() or DEFAULT_VOICE

        # 3. 使用 QTimer 异步拉起播放，这样不会阻塞当前按钮的绘制
        speak_text = f"{title}。{content}"
        QTimer.singleShot(100, lambda: self._execute_tts(speak_text))

    def _execute_tts(self, text):
        """执行文本转语音（根据选择的音色使用不同引擎）"""
        try:
            if self.current_voice == LOCAL_TTS_VOICE:
                # 使用本地语音引擎
                self._execute_local_tts(text)
            else:
                # 使用 edge_tts
                asyncio.run(self._async_speak(text))
        except Exception as e:
            print(f"语音播放失败: {e}")
        finally:
            # 无论如何，播放结束或失败后，释放按钮
            self.btn_test_audio.setEnabled(True)
            self.btn_test_audio.setText("🔊 试听当前语音内容")

    async def _async_speak(self, text):
        """异步语音合成和播放（使用 edge_tts）"""
        # 获取当前代理配置
        proxy = self.txt_proxy.text().strip() if self.txt_proxy else None
        if not proxy:
            proxy = self.current_proxy
        
        try:
            # 初始化 edge-tts 的通信对象
            communicate = edge_tts.Communicate(text, self.current_voice, proxy=proxy)

            # 创建一个内存字节流容器
            audio_data = bytearray()

            print("正在获取音频数据...")
            # 异步获取音频数据块并拼接
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_data.extend(chunk["data"])

            if not audio_data:
                print("未获取到音频数据")
                QMessageBox.warning(self, "语音合成失败", "未获取到音频数据，请检查网络连接或代理设置。")
                return

            print(f"获取到音频数据: {len(audio_data)} bytes")
            
            # 初始化 pygame 的音频混音器
            pygame.mixer.init()

            # 将内存中的字节数据转换为类文件对象，并加载到播放器
            sound_file = io.BytesIO(audio_data)
            pygame.mixer.music.load(sound_file)

            # 开始播放
            pygame.mixer.music.play()

            # 等待音频播放完毕
            while pygame.mixer.music.get_busy():
                await asyncio.sleep(0.1)

            # 清理 pygame
            pygame.mixer.quit()
            print("播放完毕")
        except Exception as e:
            print(f"异步语音播放失败: {e}")
            pygame.mixer.quit()
            error_msg = f"网络连接失败，请检查网络设置。\n\n错误信息: {str(e)}"
            QMessageBox.warning(self, "语音合成失败", error_msg)

    def _execute_local_tts(self, text):
        """使用本地语音引擎播放"""
        try:
            # 每次播放都重新初始化引擎，避免状态问题
            if self.tts_engine:
                try:
                    self.tts_engine.stop()
                except Exception:
                    pass
                self.tts_engine = None
            
            import pyttsx3
            self.tts_engine = pyttsx3.init()
            self.tts_engine.setProperty('rate', 170)
            self.tts_engine.setProperty('volume', 1.0)
            
            self.tts_engine.say(text)
            self.tts_engine.runAndWait()
            print("使用本地语音引擎播放成功")
        except Exception as e:
            print(f"本地语音引擎播放失败: {e}")
            # 重置引擎状态
            self.tts_engine = None
            QMessageBox.warning(self, "语音播放失败", f"本地语音引擎初始化失败: {str(e)}")

    # ================= 托盘图标核心逻辑 =================
    def create_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.app_icon)
        self.tray_icon.setToolTip("智能语音提醒助手")

        tray_menu = QMenu()
        show_action = QAction("显示主窗口", self)
        show_action.triggered.connect(self.show_window)
        tray_menu.addAction(show_action)

        exit_action = QAction("完全退出程序", self)
        exit_action.triggered.connect(self.quit_app)
        tray_menu.addAction(exit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        self.tray_icon.show()

    def on_tray_icon_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def show_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        self.tray_icon.hide()
        QApplication.quit()

    def closeEvent(self, event):
        close_action = self.combo_close_action.currentIndex()
        
        if close_action == 1:  # 最小化到托盘
            if self.tray_icon.isVisible():
                self.hide()
                event.ignore()
            else:
                event.accept()
        elif close_action == 2:  # 直接退出
            self.tray_icon.hide()
            event.accept()
        else:  # 询问
            reply = QMessageBox.question(
                self,
                "确认关闭",
                "您确定要关闭软件吗？\n\n选择\"最小化\"将使软件在后台继续运行。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No  # 默认选项（最小化）
            )
            
            if reply == QMessageBox.StandardButton.No:  # 点击 No，选择最小化
                if self.tray_icon.isVisible():
                    self.hide()
                    event.ignore()
                else:
                    event.accept()
            else:  # 点击 Yes，选择退出
                self.tray_icon.hide()
                event.accept()

    def save_tasks_to_json(self):
        try:
            data = {
                "settings": {
                    "close_action": self.combo_close_action.currentIndex(),
                    "voice": self.combo_voice.currentData() or DEFAULT_VOICE,
                    "proxy": self.txt_proxy.text().strip() if self.txt_proxy else ""
                },
                "tasks": [t.to_dict() for t in self.tasks]
            }
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"数据持久化写入失败: {e}")

    def load_tasks_from_json(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    
                    # 加载设置
                    if isinstance(data, dict) and "settings" in data:
                        self.combo_close_action.setCurrentIndex(data["settings"].get("close_action", 0))
                        # 加载音色设置
                        voice = data["settings"].get("voice", DEFAULT_VOICE)
                        self.current_voice = voice
                        index = self.combo_voice.findData(voice)
                        if index >= 0:
                            self.combo_voice.setCurrentIndex(index)
                        # 加载代理设置
                        proxy = data["settings"].get("proxy", "")
                        if self.txt_proxy:
                            self.txt_proxy.setText(proxy)
                            self.current_proxy = proxy
                        tasks = data.get("tasks", [])
                    else:
                        # 兼容旧格式（纯任务列表）
                        tasks = data
                    
                    # 加载任务
                    for item in tasks:
                        task = ReminderTask.from_dict(item)
                        self.tasks.append(task)
                        self.list_widget.addItem(str(task))
            except Exception as e:
                print(f"解析本地存储失败: {e}")
        else:
            # 自动生成默认配置文件
            print("配置文件不存在，正在创建默认配置...")
            self.save_tasks_to_json()

    def check_startup_status(self):
        if sys.platform != "win32":
            self.chk_startup.setEnabled(False)
            self.chk_startup.setText("自启功能 (仅支持Windows)")
            return
        try:
            import winreg
            hkey, path = get_reg_key()
            key = winreg.OpenKey(hkey, path, 0, winreg.KEY_READ)
            try:
                winreg.QueryValueEx(key, "BroadcastReminderApp")
                self.chk_startup.setChecked(True)
            except FileNotFoundError:
                self.chk_startup.setChecked(False)
            winreg.CloseKey(key)
        except Exception as e:
            print(f"查询注册表自启状态错误: {e}")

    def toggle_startup(self):
        if sys.platform != "win32":
            return
        import winreg
        hkey, path = get_reg_key()

        if self.chk_startup.isChecked():
            app_path = os.path.abspath(sys.argv[0])
            if app_path.endswith('.py'):
                cmd = f'"{sys.executable}" "{app_path}"'
            else:
                cmd = f'"{app_path}"'
            try:
                key = winreg.OpenKey(hkey, path, 0, winreg.KEY_WRITE)
                winreg.SetValueEx(key, "BroadcastReminderApp", 0, winreg.REG_SZ, cmd)
                winreg.CloseKey(key)
                QMessageBox.information(self, "自启设置", "开机自启已成功开启！")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"开机自启写入注册表失败: {e}")
                self.chk_startup.setChecked(False)
        else:
            try:
                key = winreg.OpenKey(hkey, path, 0, winreg.KEY_WRITE)
                try:
                    winreg.DeleteValue(key, "BroadcastReminderApp")
                except FileNotFoundError:
                    pass
                winreg.CloseKey(key)
                QMessageBox.information(self, "自启设置", "开机自启已关闭！")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"从注册表删除自启失败: {e}")
                self.chk_startup.setChecked(True)

    def switch_mode_ui(self):
        if self.rad_countdown.isChecked():
            self.countdown_widget.show()
            self.timing_widget.hide()
        else:
            self.countdown_widget.hide()
            self.timing_widget.show()

    def switch_repeat_spin(self):
        self.spin_repeat_count.setEnabled(self.combo_repeat.currentIndex() == 2)

    def toggle_test_btn_visibility(self):
        """根据选择的提醒方式，决定测试按钮和音色选择是否可用"""
        is_audio_mode = self.combo_type.currentIndex() == 1
        is_notification_mode = self.combo_type.currentIndex() == 0
        
        self.btn_test_audio.setEnabled(is_audio_mode)
        self.btn_test_audio.setVisible(is_audio_mode)
        
        self.btn_test_notification.setEnabled(is_notification_mode)
        self.btn_test_notification.setVisible(is_notification_mode)
        
        # 音色选择仅在语音朗读模式下显示
        self.voice_widget.setVisible(is_audio_mode)
        self.voice_widget.setEnabled(is_audio_mode)

    def test_notification(self):
        """测试通知弹窗"""
        title = self.txt_title.text().strip()
        content = self.txt_content.text().strip()

        if not title and not content:
            title = "测试提醒"
            content = "这是一条测试通知弹窗。"

        notification.notify(
            title=title,
            message=content if content else "您设定的提醒时间到了！",
            app_name="提醒助手",
            timeout=10
        )

    def add_task(self):
        title = self.txt_title.text().strip()
        content = self.txt_content.text().strip()
        if not title:
            QMessageBox.warning(self, "错误", "请输入提醒标题！")
            return

        remind_type = "通知弹窗" if self.combo_type.currentIndex() == 0 else "语音朗读"

        if self.rad_countdown.isChecked():
            mode = "倒计时"
            interval = self.spin_interval.value()
            target_time_str = ""
            repeat_idx = self.combo_repeat.currentIndex()
            if repeat_idx == 0:
                repeat_count = 0
            elif repeat_idx == 1:
                repeat_count = -1
            else:
                repeat_count = self.spin_repeat_count.value()
        else:
            mode = "定时提醒"
            interval = 0
            target_time_str = self.time_edit.time().toString("HH:mm")
            repeat_count = -1

        # 获取当前选择的音色
        voice = self.combo_voice.currentData() if self.combo_voice else DEFAULT_VOICE
        
        task = ReminderTask(title, content, remind_type, mode, target_time_str, interval, repeat_count, voice)
        self.tasks.append(task)
        self.list_widget.addItem(str(task))
        self.save_tasks_to_json()
        self.txt_title.clear()
        self.txt_content.clear()
        QMessageBox.information(self, "成功", "任务已保存并载入后台监听！")

    def delete_task(self):
        current_row = self.list_widget.currentRow()
        if current_row >= 0:
            self.tasks.pop(current_row)
            self.list_widget.takeItem(current_row)
            self.save_tasks_to_json()
        else:
            QMessageBox.warning(self, "提示", "请先在列表中选中一个任务！")

    def check_tasks(self):
        now = time.time()
        current_q_time = QTime.currentTime()
        current_time_str = current_q_time.toString("HH:mm")

        for task in self.tasks[:]:
            if not task.is_active:
                continue

            trigger = False
            if task.mode == "定时提醒":
                if current_time_str == task.target_time_str and current_q_time.second() == 0:
                    trigger = True
            else:
                if now >= task.next_trigger_timestamp:
                    trigger = True

            if trigger:
                self.trigger_alarm(task)
                if task.mode == "倒计时":
                    task.triggered_count += 1
                    if task.repeat_count == 0:
                        task.is_active = False
                        self.remove_task_object(task)
                    elif 0 < task.repeat_count <= task.triggered_count:
                        task.is_active = False
                        self.remove_task_object(task)
                    else:
                        task.next_trigger_timestamp = now + (task.interval_min * 60)
                self.save_tasks_to_json()

    def remove_task_object(self, task):
        if task in self.tasks:
            idx = self.tasks.index(task)
            self.tasks.remove(task)
            self.list_widget.takeItem(idx)

    def trigger_alarm(self, task):
        if task.remind_type == "通知弹窗":
            notification.notify(
                title=task.title,
                message=task.content if task.content else "您设定的提醒时间到了！",
                app_name="提醒助手",
                timeout=10
            )
        elif task.remind_type == "语音朗读":
            speak_text = f"{task.title}。{task.content}"
            print(f"🔊 正在朗读: {speak_text}")
            try:
                # 使用任务特定的音色进行语音合成
                self.current_voice = task.voice
                if task.voice == LOCAL_TTS_VOICE:
                    # 使用本地语音引擎
                    self._execute_local_tts(speak_text)
                else:
                    # 使用 edge_tts
                    asyncio.run(self._async_speak(speak_text))
            except Exception as e:
                print(f"语音朗读失败: {e}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ReminderApp()
    window.show()
    sys.exit(app.exec())