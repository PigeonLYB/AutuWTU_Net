import requests
import time
import json
import os
import sys
import socket
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import pystray
from pystray import MenuItem as item
import urllib3
from PIL import Image, ImageTk
import webbrowser
import re
from urllib.parse import urljoin
import winreg


def get_app_directory():
    """返回程序目录，兼容脚本模式与 PyInstaller 打包模式。"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))


APP_DIR = get_app_directory()
os.chdir(APP_DIR)


DEFAULT_LOCK_PORT = 29666
MAX_LOGIN_RETRIES = 3  # 最大重试次数
RETRY_DELAY = 5  # 重试间隔（秒）
CONFIG_PATH = os.path.join(APP_DIR, "wifi_config.json")
LOG_PATH = os.path.join(APP_DIR, "debug.log")
ICON_NAME = "icon.ico"
TITLE_IMG = "title.png"
AUTHOR_URL = "https://github.com/PigeonLYB/AutoWTU_Net"
current_config = {
    "userId": "",
    "password": "",
    "service": "DX",
    "port": DEFAULT_LOCK_PORT,
    "interval": 5,
    "startup_delay": 30,
    "login_retries": MAX_LOGIN_RETRIES,
    "retry_delay": RETRY_DELAY,
    "auto_start": False
}

stop_event = threading.Event()
login_lock = threading.Lock()
config_window_active = False
NO_PROXIES = {"http": None, "https": None}

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def get_app_path():
    """返回当前启动入口路径，用于注册表开机自启项校验。"""
    if getattr(sys, 'frozen', False):
        return sys.executable
    else:
        return os.path.abspath(sys.argv[0])

def is_auto_start_enabled():
    """检查注册表开机自启是否已指向当前程序。"""
    try:
        app_path = os.path.normcase(os.path.normpath(get_app_path()))
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ
        )
        try:
            value, _ = winreg.QueryValueEx(key, "AutoWTU")
            startup_path = str(value).strip().strip('"')
            startup_path = os.path.normcase(os.path.normpath(startup_path))
            return startup_path == app_path
        except FileNotFoundError:
            return False
        finally:
            winreg.CloseKey(key)
    except Exception as e:
        write_log(f"检查开机自启失败: {e}")
        return False

def set_auto_start(enable):
    """写入或删除 Windows 开机自启注册表项。"""
    key = None
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE
        )

        if enable:
            app_path = get_app_path()
            winreg.SetValueEx(key, "AutoWTU", 0, winreg.REG_SZ, f'"{app_path}"')
            write_log("已设置开机自启")
        else:
            try:
                winreg.DeleteValue(key, "AutoWTU")
                write_log("已取消开机自启")
            except FileNotFoundError:
                pass

        return True
    except Exception as e:
        write_log(f"设置开机自启失败: {e}")
        return False
    finally:
        if key:
            winreg.CloseKey(key)

def toggle_auto_start():
    """切换开机自启并立即同步写入配置文件。"""
    new_state = not current_config.get("auto_start", False)
    if set_auto_start(new_state):
        current_config["auto_start"] = new_state
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(current_config, f, ensure_ascii=False, indent=2)
        return True
    return False


def write_log(message):
    """输出控制台日志，并追加写入 debug.log。"""
    ts = time.strftime("[%Y-%m-%d %H:%M:%S] ")
    msg = ts + str(message)
    print(msg)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def resource_path(relative_path):
    """解析资源路径，兼容 PyInstaller 的 _MEIPASS 临时目录。"""
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = APP_DIR
    return os.path.join(base_path, relative_path)


def load_config():
    """加载配置文件，并校准 auto_start 与注册表实际状态。"""
    global current_config
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    current_config.update(data)
                    actual_state = is_auto_start_enabled()
                    if current_config.get("auto_start", False) != actual_state:
                        current_config["auto_start"] = actual_state
                    return True
        except Exception as e:
            write_log(f"读取配置失败: {e}")
            return False
    return False


def save_config(uid, pwd, serv, port, interval, startup_delay, login_retries, retry_delay, auto_start=None):
    """保存用户配置；startup_delay 的单位为秒。"""
    global current_config
    current_config.update({
        "userId": uid.strip(),
        "password": pwd,
        "service": serv,
        "port": int(port),
        "interval": int(interval),
        "startup_delay": int(startup_delay),
        "login_retries": int(login_retries),
        "retry_delay": int(retry_delay)
    })
    if auto_start is not None:
        current_config["auto_start"] = auto_start

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(current_config, f, ensure_ascii=False, indent=2)


def check_single_instance():
    """通过本地端口锁防止重复启动。"""
    try:
        global _lock_socket
        _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _lock_socket.bind(("127.0.0.1", current_config.get("port", DEFAULT_LOCK_PORT)))
        return True
    except OSError:
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror("提示", "AutoWTU 校园网助手已经在运行中！")
        r.destroy()
        return False


def is_network_ok():
    """进行轻量联网探测，判断当前是否已可正常外网访问。"""
    tests = [
        ("http://www.msftconnecttest.com/connecttest.txt", "Microsoft Connect Test"),
        ("http://connectivitycheck.gstatic.com/generate_204", None),
    ]

    for url, expected in tests:
        try:
            r = requests.get(
                url,
                timeout=(3, 3),
                allow_redirects=False,
                proxies=NO_PROXIES
            )

            if expected:
                if r.status_code == 200 and expected in r.text:
                    return True
            else:
                if r.status_code == 204:
                    return True
        except Exception:
            pass

    return False


def extract_eportal_url_from_html(base_url, html):
    """从 HTML/JS 跳转脚本中提取 ePortal 认证入口地址。"""
    patterns = [
        r'''(?:top\.self|window|top)\.location(?:\.href)?\s*=\s*["']([^"']+)["']''',
        r'''(?<![\w.])location(?:\.href)?\s*=\s*["']([^"']+)["']''',
        r'''url\s*=\s*["']?([^"'>\s]+)''',
    ]

    found_urls = []
    for p in patterns:
        matches = re.findall(p, html, re.IGNORECASE)
        for m in matches:
            full_url = urljoin(base_url, m)
            found_urls.append(full_url)

    for url in found_urls:
        if "?" in url and ("index.jsp" in url.lower() or "eportal" in url.lower()):
            return url

    for url in found_urls:
        if "eportal" in url.lower():
            return url

    return found_urls[0] if found_urls else None


def dump_probe_debug(resp):
    try:
        write_log(f"响应头: {dict(resp.headers)}")
    except Exception:
        pass

    try:
        text_preview = (resp.text or "")[:300].replace("\r", " ").replace("\n", " ")
        if text_preview:
            write_log(f"响应体前300字: {text_preview}")
    except Exception:
        pass


def detect_portal_url(session, headers):
    """探测认证入口，优先返回带 queryString 参数的 URL。"""
    probe_urls = [
        "http://www.msftconnecttest.com/connecttest.txt",
        "http://connectivitycheck.gstatic.com/generate_204",
        "http://1.1.1.1",
        "http://www.baidu.com",
        "http://172.30.1.111",
    ]

    write_log("开始精准探测认证地址...")
    fallback_url = ""

    for url in probe_urls:
        try:
            write_log(f"探测: {url}")
            resp = session.get(
                url,
                headers=headers,
                timeout=(4, 6),
                allow_redirects=True,
                proxies=NO_PROXIES
            )

            if "?" in resp.url and ("index.jsp" in resp.url.lower() or "eportal" in resp.url.lower()):
                write_log(f"通过跳转直接获取 URL: {resp.url}")
                return resp.url

            # 部分校园网会返回 200 页面，再通过 JS location 跳转。
            text = resp.text or ""
            html_url = extract_eportal_url_from_html(resp.url, text)
            if html_url:
                write_log(f"从源码 JS 提取到 URL: {html_url}")
                if "?" in html_url:
                    return html_url
                fallback_url = html_url

            loc = resp.headers.get("Location")
            if loc:
                full_loc = urljoin(resp.url, loc)
                if "?" in full_loc:
                    return full_loc
                fallback_url = full_loc

        except Exception as e:
            write_log(f"探测 {url} 发生异常: {e}")

    return fallback_url


def do_login():
    """执行一次登录流程；互斥锁可避免并发重复提交登录请求。"""
    if not login_lock.acquire(blocking=False):
        return "已有登录任务正在执行..."

    try:
        uid = current_config.get("userId", "").strip()
        pwd = current_config.get("password", "")
        serv = current_config.get("service", "DX")

        if not uid or not pwd:
            return "未配置账密"

        session = requests.Session()
        session.trust_env = False

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9"
        }

        target_url = detect_portal_url(session, headers)

        if not target_url:
            return "无法探测到认证页面，请确认已连接校园网 WiFi"

        lower_target = target_url.lower()
        if "success.jsp" in lower_target or "redirectortosuccess" in lower_target:
            write_log(f"检测到已在线网址: {target_url}")
            return "已成功连接到校园网 (检测到成功页)"

        if "?" not in target_url:
            return f"获取到的 URL 缺少参数: {target_url}"

        try:
            # queryString 为网关接口必需参数，来自认证入口 URL 的查询串。
            qs = target_url.split("?", 1)[1]

            headers.update({
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": target_url,
                "Origin": "http://172.30.1.111"
            })

            data = {
                "userId": uid,
                "password": pwd,
                "service": serv,
                "queryString": qs,
                "operatorPwd": "",
                "operatorUserId": "",
                "validcode": "",
                "passwordEncrypt": "false"
            }

            write_log(f"发送登录表单... 参数长度: {len(qs)}")

            response = session.post(
                "http://172.30.1.111/eportal/InterFace.do",
                headers=headers,
                params={"method": "login"},
                data=data,
                timeout=(5, 10),
                proxies=NO_PROXIES
            )

            res_text = response.text
            write_log(f"服务器响应正文: {res_text[:200]}")

            try:
                j = response.json()
                result = str(j.get("result", "")).lower()
                msg = j.get("message") or j.get("msg") or res_text

                if result == "success":
                    return "登录成功"
                elif result in ("already", "online", "wait"):
                    return f"可能已在线: {msg}"
                else:
                    return f"登录失败: {msg}"

            except Exception:
                text = res_text.lower()
                if '"result":"success"' in text or "success" in text:
                    return "登录成功"
                if "already online" in text or "已在线" in text:
                    return "系统提示已在线"
                return f"返回详情: {res_text[:120]}"

        except Exception as e:
            return f"POST 异常: {e}"

    finally:
        login_lock.release()


def do_login_with_retry(is_first_attempt=False):
    """带重试功能的登录函数。"""
    retry_delay = max(1, int(current_config.get("retry_delay", RETRY_DELAY)))

    if is_first_attempt:
        # 开机第一次尝试：失败后仅重试 1 次，并在最终失败时弹窗提示。
        max_retries = 1
        show_dialog_on_failure = True
    else:
        # 常规后台重试：使用可配置重试次数，不弹窗打扰用户。
        max_retries = max(0, int(current_config.get("login_retries", MAX_LOGIN_RETRIES)))
        show_dialog_on_failure = False

    last_message = ""

    for attempt in range(max_retries + 1):
        if attempt > 0:
            write_log(f"登录失败，{retry_delay}秒后进行第{attempt}次重试...")
            time.sleep(retry_delay)

        result = do_login()
        last_message = result
        write_log(f"登录尝试 #{attempt + 1}: {result}")

        if "成功" in result or "已在线" in result:
            return True, result

        if attempt == max_retries and show_dialog_on_failure:
            # 独立线程弹窗，避免阻塞守护线程。
            def show_error_dialog():
                root = tk.Tk()
                root.withdraw()
                messagebox.showerror(
                    "连接失败",
                    "开机首次连接校园网失败！\n\n"
                    f"错误信息: {result}\n\n"
                    "请检查:\n"
                    "1. 账号密码是否正确\n"
                    "2. 是否已连接校园网WiFi\n"
                    "3. 网络环境是否正常\n\n"
                    "程序将继续在后台尝试重连。"
                )
                root.destroy()

            threading.Thread(target=show_error_dialog, daemon=True).start()

    return False, last_message


def show_config_window():
    """配置窗口：账号、运营商、检测间隔、开机延时、自启动。"""
    global config_window_active
    if config_window_active:
        return
    config_window_active = True

    root = tk.Tk()
    root.title("AutoWTU 校园网助手")
    root.resizable(False, False)

    try:
        root.iconbitmap(resource_path(ICON_NAME))
    except Exception:
        pass

    def on_close():
        global config_window_active
        config_window_active = False
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    try:
        img_raw = Image.open(resource_path(TITLE_IMG))
        tw = 380
        th = int(tw / (img_raw.size[0] / img_raw.size[1]))
        img_tk = ImageTk.PhotoImage(img_raw.resize((tw, th), Image.Resampling.LANCZOS))
        label_img = tk.Label(root, image=img_tk)
        label_img.image = img_tk  # 保持引用，避免 Tk 图片被回收后不显示。
        label_img.pack()
        root.geometry(f"380x{580 + th}")
    except Exception:
        root.geometry("380x670")

    frame = tk.Frame(root)
    frame.pack(pady=10)

    tk.Label(frame, text="账号:").grid(row=0, column=0, pady=5, sticky="e")
    u_v = tk.StringVar(value=current_config.get("userId", ""))
    tk.Entry(frame, textvariable=u_v, width=25).grid(row=0, column=1)

    tk.Label(frame, text="密码:").grid(row=1, column=0, pady=5, sticky="e")
    p_v = tk.StringVar(value=current_config.get("password", ""))
    tk.Entry(frame, textvariable=p_v, show="*", width=25).grid(row=1, column=1)

    carrier_map = {"电信": "DX", "联通": "LT", "移动": "YD", "校园网": "XYW"}
    code_to_name = {v: k for k, v in carrier_map.items()}

    tk.Label(frame, text="运营商:").grid(row=2, column=0, pady=5, sticky="e")
    s_v = tk.StringVar()
    s_v.set(code_to_name.get(current_config.get("service", "DX"), "电信"))

    ttk.Combobox(
        frame,
        textvariable=s_v,
        values=list(carrier_map.keys()),
        state="readonly",
        width=23
    ).grid(row=2, column=1)

    tk.Label(frame, text="检测间隔(分):").grid(row=3, column=0, pady=5, sticky="e")
    i_v = tk.StringVar(value=str(current_config.get("interval", 5)))
    tk.Spinbox(frame, from_=1, to=60, textvariable=i_v, width=23).grid(row=3, column=1)

    tk.Label(frame, text="防多开端口:").grid(row=4, column=0, pady=5, sticky="e")
    port_v = tk.StringVar(value=str(current_config.get("port", DEFAULT_LOCK_PORT)))
    tk.Entry(frame, textvariable=port_v, width=25).grid(row=4, column=1)

    tk.Label(frame, text="开机启动延时(秒):").grid(row=5, column=0, pady=5, sticky="e")
    startup_delay_v = tk.StringVar(value=str(current_config.get("startup_delay", 30)))
    tk.Spinbox(frame, from_=0, to=3600, textvariable=startup_delay_v, width=23).grid(row=5, column=1)
    tk.Label(frame, text="(0-3600秒, 等待网络稳定)", font=("", 8), fg="gray").grid(row=6, column=1, sticky="w")

    tk.Label(frame, text="登录重试次数:").grid(row=7, column=0, pady=5, sticky="e")
    retries_v = tk.StringVar(value=str(current_config.get("login_retries", MAX_LOGIN_RETRIES)))
    tk.Spinbox(frame, from_=0, to=10, textvariable=retries_v, width=23).grid(row=7, column=1)

    tk.Label(frame, text="重试间隔(秒):").grid(row=8, column=0, pady=5, sticky="e")
    retry_delay_v = tk.StringVar(value=str(current_config.get("retry_delay", RETRY_DELAY)))
    tk.Spinbox(frame, from_=1, to=60, textvariable=retry_delay_v, width=23).grid(row=8, column=1)
    tk.Label(frame, text="(首次重试与后台重试共用)", font=("", 8), fg="gray").grid(row=9, column=1, sticky="w")

    auto_start_var = tk.BooleanVar(value=current_config.get("auto_start", False))
    tk.Checkbutton(
        frame,
        text="开机自动启动",
        variable=auto_start_var,
        command=lambda: None
    ).grid(row=10, column=1, pady=10, sticky="w")

    def thread_test():
        """使用当前输入做一次即时登录测试，不覆盖已保存配置。"""
        def _run():
            try:
                btn_test.config(state="disabled", text="正在探测...")
                temp_uid = u_v.get()
                temp_pwd = p_v.get()
                temp_serv = carrier_map[s_v.get()]

                orig_uid = current_config.get("userId", "")
                orig_pwd = current_config.get("password", "")
                orig_serv = current_config.get("service", "DX")

                current_config["userId"] = temp_uid
                current_config["password"] = temp_pwd
                current_config["service"] = temp_serv

                res = do_login()
                write_log(f"[手动测试] 登录结果: {res}")
                messagebox.showinfo("测试结果", res)

                current_config["userId"] = orig_uid
                current_config["password"] = orig_pwd
                current_config["service"] = orig_serv

            except Exception as e:
                write_log(f"[手动测试] 异常: {e}")
                messagebox.showerror("错误", f"测试失败：{e}")
            finally:
                btn_test.config(state="normal", text="立即测试登录")

        threading.Thread(target=_run, daemon=True).start()

    def on_save():
        try:
            auto_start = auto_start_var.get()

            save_config(
                u_v.get(),
                p_v.get(),
                carrier_map[s_v.get()],
                port_v.get(),
                i_v.get(),
                startup_delay_v.get(),
                retries_v.get(),
                retry_delay_v.get(),
                auto_start
            )

            if auto_start != is_auto_start_enabled():
                set_auto_start(auto_start)

            messagebox.showinfo("提示", "配置已保存，程序将在后台运行。")
            on_close()
        except Exception as e:
            messagebox.showerror("错误", f"保存失败：{e}")

    btn_test = tk.Button(root, text="立即测试登录", command=thread_test, bg="#f0ad4e", width=25)
    btn_test.pack(pady=5)

    tk.Button(root, text="保存并后台运行", command=on_save, bg="#0078d7", fg="white", width=25).pack(pady=5)

    link = tk.Label(
        root,
        text="开源软件，由 Pigeon_LYB 制作 (点击跳转)",
        fg="#0056b3",
        cursor="hand2",
        font=("", 9, "underline")
    )
    link.pack(side="bottom", pady=15)
    link.bind("<Button-1>", lambda e: webbrowser.open(AUTHOR_URL))

    root.mainloop()


def worker():
    """后台守护：按间隔检测网络，离线则自动尝试登录（带重试机制）。"""
    write_log("=== 后台守护线程开启 ===")

    # 标记是否为开机后的第一次离线登录尝试
    is_first_check = True

    # 开机初期网络常未稳定，按配置延时后再进入探测逻辑。
    delay_seconds = max(0, min(3600, int(current_config.get("startup_delay", 30))))
    if delay_seconds > 0:
        write_log(f"启动延迟 {delay_seconds} 秒，等待网络初始化...")
        for _ in range(delay_seconds):
            if stop_event.is_set():
                return
            time.sleep(1)

    while not stop_event.is_set():
        try:
            if not is_network_ok():
                write_log("检测到网络离线，尝试自动登录...")

                if is_first_check:
                    success, message = do_login_with_retry(is_first_attempt=True)
                    is_first_check = False
                else:
                    success, message = do_login_with_retry(is_first_attempt=False)

                write_log(f"登录结果: {message}")
            else:
                write_log("网络正常，无需登录。")
        except Exception as e:
            write_log(f"后台守护异常: {e}")

        wait_seconds = max(1, int(current_config.get("interval", 5)) * 60)
        for _ in range(wait_seconds):
            if stop_event.is_set():
                break
            time.sleep(1)


def run_tray():
    """初始化系统托盘菜单并进入事件循环。"""
    try:
        img = Image.open(resource_path(ICON_NAME))
    except Exception:
        img = Image.new('RGB', (64, 64), (0, 120, 215))

    def on_exit(icon):
        write_log("收到退出请求，程序即将退出。")
        stop_event.set()
        icon.stop()
        os._exit(0)

    def open_settings(icon=None):
        threading.Thread(target=show_config_window, daemon=True).start()

    def test_login():
        """手动测试登录，使用重试机制但不弹窗。"""
        def _test():
            success, message = do_login_with_retry(is_first_attempt=False)
            root = tk.Tk()
            root.withdraw()
            if success:
                messagebox.showinfo("测试结果", f"✓ {message}")
            else:
                messagebox.showerror("测试结果", f"✗ {message}")
            root.destroy()

        threading.Thread(target=_test, daemon=True).start()

    def toggle_startup():
        if toggle_auto_start():
            state = "已启用" if current_config["auto_start"] else "已禁用"
            write_log(f"开机自启{state}")
        else:
            write_log("开机自启设置失败")

    menu = pystray.Menu(
        item("AutoWTU 校园网助手", lambda: None, enabled=False),
        item("设置中心", open_settings, default=True),
        item("立即测试登录", lambda: test_login()),
        item("开机自启", toggle_startup, checked=lambda item: current_config.get("auto_start", False)),
        item("退出程序", on_exit)
    )

    pystray.Icon("AutoWTU", img, "校园网自动重连助手", menu, action=lambda: open_settings()).run()


def main():
    """程序入口：加载配置、单实例检查、启动守护线程和托盘。"""
    write_log("=== AutoWTU 启动 ===")

    load_config()

    config_auto = current_config.get("auto_start", False)
    actual_auto = is_auto_start_enabled()
    if config_auto != actual_auto:
        set_auto_start(config_auto)

    if not check_single_instance():
        return

    if not current_config.get("userId"):
        show_config_window()

    if not current_config.get("userId"):
        write_log("未配置账号，程序退出。")
        return

    threading.Thread(target=worker, daemon=True).start()

    run_tray()


if __name__ == "__main__":
    main()