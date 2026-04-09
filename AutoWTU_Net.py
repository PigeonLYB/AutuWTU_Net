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
import winreg  # 新增：用于Windows注册表操作


def get_app_directory():
    """获取应用程序所在目录（exe或脚本所在目录）"""
    if getattr(sys, 'frozen', False):
        # 打包后的 exe
        return os.path.dirname(sys.executable)
    else:
        # Python 脚本
        return os.path.dirname(os.path.abspath(__file__))


# 设置工作目录为程序所在目录
APP_DIR = get_app_directory()
os.chdir(APP_DIR)


# ==================== 全局配置 ====================
DEFAULT_LOCK_PORT = 29666  # 避开常见服务端口，降低冲突概率
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
    "startup_delay": 5,
    "auto_start": False  # 新增：开机自启配置项
}

stop_event = threading.Event()
login_lock = threading.Lock()  # 登录锁，防止多线程同时登录
config_window_active = False
NO_PROXIES = {"http": None, "https": None}

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ==================== 开机自启管理 ====================
def get_app_path():
    """获取当前程序的完整路径"""
    if getattr(sys, 'frozen', False):
        # 打包后的exe路径
        return sys.executable
    else:
        # Python脚本路径
        return os.path.abspath(sys.argv[0])

def is_auto_start_enabled():
    """检查是否已设置开机自启"""
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
            # 注册表中可能是带引号的可执行路径
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
    """设置或取消开机自启"""
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
            # 关键：加引号防止空格路径失效
            winreg.SetValueEx(key, "AutoWTU", 0, winreg.REG_SZ, f'"{app_path}"')
            write_log("已设置开机自启")
        else:
            try:
                winreg.DeleteValue(key, "AutoWTU")
                write_log("已取消开机自启")
            except FileNotFoundError:
                pass  # 本来就没有

        return True
    except Exception as e:
        write_log(f"设置开机自启失败: {e}")
        return False
    finally:
        if key:
            winreg.CloseKey(key)

def toggle_auto_start():
    """切换开机自启状态"""
    new_state = not current_config.get("auto_start", False)
    if set_auto_start(new_state):
        current_config["auto_start"] = new_state
        # 保存到配置文件
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(current_config, f, ensure_ascii=False, indent=2)
        return True
    return False


# ==================== 日志 ====================
def write_log(message):
    """同步写入日志"""
    ts = time.strftime("[%Y-%m-%d %H:%M:%S] ")
    msg = ts + str(message)
    print(msg)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def resource_path(relative_path):
    """兼容 PyInstaller 打包路径"""
    if getattr(sys, 'frozen', False):
        # 打包后，资源文件在 _MEIPASS 临时目录
        base_path = sys._MEIPASS
    else:
        base_path = APP_DIR
    return os.path.join(base_path, relative_path)


# ==================== 配置 ====================
def load_config():
    global current_config
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    current_config.update(data)
                    # 同步注册表状态
                    actual_state = is_auto_start_enabled()
                    if current_config.get("auto_start", False) != actual_state:
                        current_config["auto_start"] = actual_state
                    return True
        except Exception as e:
            write_log(f"读取配置失败: {e}")
            return False
    return False


def save_config(uid, pwd, serv, port, interval, auto_start=None):
    global current_config
    current_config.update({
        "userId": uid.strip(),
        "password": pwd,
        "service": serv,
        "port": int(port),
        "interval": int(interval)
    })
    if auto_start is not None:
        current_config["auto_start"] = auto_start

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(current_config, f, ensure_ascii=False, indent=2)


def check_single_instance():
    """防多开：绑定本地端口"""
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


# ==================== 网络检测 ====================
def is_network_ok():
    """
    多目标联网检测
    """
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


# ==================== 认证链接提取 ====================
def extract_eportal_url_from_html(base_url, html):
    """
    专门针对 JS 强转页面（top.self.location.href 等）进行提取
    """
    patterns = [
        # 兼容 top.self.location.href / window.location.href / top.location
        r'''(?:top\.self|window|top)\.location(?:\.href)?\s*=\s*["']([^"']+)["']''',
        # 兼容普通的 location.href
        r'''(?<![\w.])location(?:\.href)?\s*=\s*["']([^"']+)["']''',
        # meta refresh 标签
        r'''url\s*=\s*["']?([^"'>\s]+)''',
    ]

    found_urls = []
    for p in patterns:
        matches = re.findall(p, html, re.IGNORECASE)
        for m in matches:
            full_url = urljoin(base_url, m)
            found_urls.append(full_url)

    # 优先级排序：优先返回带 '?' 的链接，因为认证必须有 queryString
    for url in found_urls:
        if "?" in url and ("index.jsp" in url.lower() or "eportal" in url.lower()):
            return url

    # 次优先级：不带参数但看起来像 portal 的
    for url in found_urls:
        if "eportal" in url.lower():
            return url

    return found_urls[0] if found_urls else None


def dump_probe_debug(resp):
    """记录探测响应的关键信息"""
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
    """
    强化版探测：专门对付 200 状态码 + JS 跳转的校园网
    """
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
            # 允许跟随跳转，有些出口可能中途有 302
            resp = session.get(
                url,
                headers=headers,
                timeout=(4, 6),
                allow_redirects=True,
                proxies=NO_PROXIES
            )

            # 1. 最终 URL 已经直接带参数了
            if "?" in resp.url and ("index.jsp" in resp.url.lower() or "eportal" in resp.url.lower()):
                write_log(f"通过跳转直接获取 URL: {resp.url}")
                return resp.url

            # 2. 检查返回的 HTML 源码里有没有 JS 跳转
            text = resp.text or ""
            html_url = extract_eportal_url_from_html(resp.url, text)
            if html_url:
                write_log(f"从源码 JS 提取到 URL: {html_url}")
                if "?" in html_url:
                    return html_url
                fallback_url = html_url

            # 3. 备用检查 30x 重定向的 Header
            loc = resp.headers.get("Location")
            if loc:
                full_loc = urljoin(resp.url, loc)
                if "?" in full_loc:
                    return full_loc
                fallback_url = full_loc

        except Exception as e:
            write_log(f"探测 {url} 发生异常: {e}")

    return fallback_url


# ==================== 核心登录逻辑 ====================
def do_login():
    """
    登录主函数，带互斥锁
    """
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

        # 1. 探测 portal URL
        target_url = detect_portal_url(session, headers)

        if not target_url:
            return "无法探测到认证页面，请确认已连接校园网 WiFi"

        # 如果捕获到成功页面的网址，直接判定成功，不发 POST
        lower_target = target_url.lower()
        if "success.jsp" in lower_target or "redirectortosuccess" in lower_target:
            write_log(f"检测到已在线网址: {target_url}")
            return "已成功连接到校园网 (检测到成功页)"

        # 过滤占位页：如果抓到的是那个没有参数的网址且不包含上述关键词，说明提取失败
        if "?" not in target_url:
            return f"获取到的 URL 缺少参数: {target_url}"

        # 2. 提交登录
        try:
            # 提取 ? 后的所有参数
            qs = target_url.split("?", 1)[1]

            # Referer 必须是刚才抓到的完整 URL，某些服务器强制校验
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

            # 优先 JSON 解析
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
                # 文本兜底解析
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


# ==================== 界面 ====================
def show_config_window():
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

    # 顶部图片
    try:
        img_raw = Image.open(resource_path(TITLE_IMG))
        tw = 380
        th = int(tw / (img_raw.size[0] / img_raw.size[1]))
        img_tk = ImageTk.PhotoImage(img_raw.resize((tw, th), Image.Resampling.LANCZOS))
        label_img = tk.Label(root, image=img_tk)
        label_img.image = img_tk  # 防止被回收
        label_img.pack()
        root.geometry(f"380x{470 + th}")  # 增加高度以容纳新控件
    except Exception:
        root.geometry("380x560")

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

    # 新增：开机自启复选框
    auto_start_var = tk.BooleanVar(value=current_config.get("auto_start", False))
    tk.Checkbutton(
        frame,
        text="开机自动启动",
        variable=auto_start_var,
        command=lambda: None  # 点击时不立即执行，保存时才应用
    ).grid(row=5, column=1, pady=10, sticky="w")

    def thread_test():
        def _run():
            try:
                btn_test.config(state="disabled", text="正在探测...")
                # 测试时不保存配置，只使用当前输入
                temp_uid = u_v.get()
                temp_pwd = p_v.get()
                temp_serv = carrier_map[s_v.get()]

                # 临时保存原始配置
                orig_uid = current_config.get("userId", "")
                orig_pwd = current_config.get("password", "")
                orig_serv = current_config.get("service", "DX")

                # 临时修改配置用于测试
                current_config["userId"] = temp_uid
                current_config["password"] = temp_pwd
                current_config["service"] = temp_serv

                res = do_login()
                write_log(f"[手动测试] 登录结果: {res}")
                messagebox.showinfo("测试结果", res)

                # 恢复原配置
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

            # 保存配置
            save_config(
                u_v.get(),
                p_v.get(),
                carrier_map[s_v.get()],
                port_v.get(),
                i_v.get(),
                auto_start
            )

            # 应用开机自启设置
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


# ==================== 后台守护 ====================
def worker():
    write_log("=== 后台守护线程开启 ===")

    # 给系统和 WiFi 留出初始化时间，避免开机瞬间探测失败
    delay_seconds = max(0, int(current_config.get("startup_delay", 5)))
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
                result = do_login()
                write_log(f"登录结果: {result}")
            else:
                write_log("网络正常，无需登录。")
        except Exception as e:
            write_log(f"后台守护异常: {e}")

        # 按分钟间隔等待
        wait_seconds = max(1, int(current_config.get("interval", 5)) * 60)
        for _ in range(wait_seconds):
            if stop_event.is_set():
                break
            time.sleep(1)


# ==================== 托盘 ====================
def run_tray():
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
        result = do_login()
        write_log(f"[托盘测试] {result}")

    def toggle_startup():
        """托盘菜单中的开机自启切换"""
        if toggle_auto_start():
            state = "已启用" if current_config["auto_start"] else "已禁用"
            write_log(f"开机自启{state}")
            # 这里无法动态更新菜单文字，但功能已生效
        else:
            write_log("开机自启设置失败")

    menu = pystray.Menu(
        item("AutoWTU 校园网助手", lambda: None, enabled=False),
        item("设置中心", open_settings, default=True),  # default=True 让双击/单击触发此项
        item("立即测试登录", lambda: threading.Thread(target=test_login, daemon=True).start()),
        item("开机自启", toggle_startup, checked=lambda item: current_config.get("auto_start", False)),
        item("退出程序", on_exit)
    )

    # action 参数确保左键单击也能触发默认菜单项
    pystray.Icon("AutoWTU", img, "校园网自动重连助手", menu, action=lambda: open_settings()).run()


# ==================== 主入口 ====================
def main():
    write_log("=== AutoWTU 启动 ===")

    load_config()

    # 确保注册表状态与配置一致
    config_auto = current_config.get("auto_start", False)
    actual_auto = is_auto_start_enabled()
    if config_auto != actual_auto:
        set_auto_start(config_auto)

    if not check_single_instance():
        return

    # 首次无配置，弹设置
    if not current_config.get("userId"):
        show_config_window()

    # 如果用户关掉窗口后仍未配置，退出
    if not current_config.get("userId"):
        write_log("未配置账号，程序退出。")
        return

    # 启动后台守护
    threading.Thread(target=worker, daemon=True).start()

    # 启动托盘
    run_tray()


if __name__ == "__main__":
    main()