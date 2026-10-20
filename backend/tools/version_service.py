# coding: utf-8
import re
import os
import sys
import json
import hashlib
import requests

from PySide6.QtCore import QVersionNumber

from backend.config import VERSION, PROJECT_UPDATE_URLS, tr


class VersionService:
    """ Version service """

    def __init__(self):
        self.current_version = VERSION
        self.lastest_version = VERSION
        self.version_pattern = re.compile(r'v*((\d+)\.(\d+)\.(\d+))')
        self.api_endpoints = PROJECT_UPDATE_URLS
        # 新版本安装包直链（来自魔搭 version.json 的 download_url；GitHub API 无此字段）
        self.download_url = None
        # 备用下载地址（夸克网盘分享链接等；同样仅魔搭清单提供）
        self.backup_url = None
        self.backup_note = None
        # 更新说明（主源说明/提取码等，随 download_url 一起来自 version.json；弹窗中展示）
        self.release_notes = None
        # 代码层补丁清单（魔搭 version.json 的 patches 数组；GitHub 端点无此字段）
        # 每项：{'id': int 序号, 'target': 适用版本, 'url': zip 直链,
        #        'sha256': 校验值, 'notes': 说明}
        self.patches = []

    def get_latest_version(self):
        """ get latest version """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36 Edg/112.0.1722.64"
        }

        proxy = self.get_system_proxy()
        proxies = {
            "http": proxy,
            "https": proxy
        }

        # 依次尝试不同的API端点（国内源在前，GitHub 兜底）
        for url in self.api_endpoints:
            try:
                response = requests.get(url, headers=headers, timeout=5, allow_redirects=True, proxies=proxies)
                response.raise_for_status()

                # 解析版本
                data = response.json()
                version = data['tag_name']  # type:str
                match = self.version_pattern.search(version)
                if not match:
                    continue  # 如果版本格式不匹配，尝试下一个API

                self.lastest_version = match.group(1)
                # 国内源清单可带安装包直链、备用地址与说明（网盘提取码等），
                # GitHub API 端点无这些字段保持 None（弹窗回退 Releases 页）
                self.download_url = data.get('download_url') or None
                self.backup_url = data.get('backup_url') or None
                self.backup_note = data.get('backup_note') or None
                self.release_notes = data.get('notes') or None
                self.patches = data.get('patches') or []
                print(tr['VersionService']['VersionInfo'].format(VERSION, self.lastest_version))
                return self.lastest_version
            except Exception as e:
                print(tr['VersionService']['RequestError'].format(url, str(e)))
                continue  # 出错时尝试下一个API

        # 所有API都失败时返回当前版本
        return VERSION

    def get_download_url(self):
        """新版本安装包直链；无（如 GitHub 端点）返回 None，调用方回退 Releases 页"""
        return self.download_url

    def get_release_notes(self):
        """更新说明（提取码/备注等），无则 None"""
        return self.release_notes

    def get_backup_url(self):
        """备用下载地址（夸克网盘等），无则 None"""
        return self.backup_url

    def get_backup_note(self):
        """备用地址说明（提取码等），无则 None"""
        return self.backup_note

    def has_new_version(self) -> bool:
        """ check whether there is a new version """
        version = QVersionNumber.fromString(self.get_latest_version())
        current_version = QVersionNumber.fromString(self.current_version)
        return version > current_version

    # ---------- 代码层补丁（小体积热更新：保留权重/运行时/配置） ----------

    def _app_root(self):
        """安装根目录（gui.py 所在层）。GUI/CLI 均以脚本路径启动，argv[0] 即入口。"""
        try:
            root = os.path.dirname(os.path.abspath(sys.argv[0]))
            return root if os.path.isdir(root) else os.getcwd()
        except Exception:
            return os.getcwd()

    def get_local_patch_level(self):
        """本地补丁级别：安装根目录 patch_level.txt（补丁应用后由入口写入；
        手动解压补丁包时该文件随包覆盖，同样生效）。"""
        try:
            with open(os.path.join(self._app_root(), 'patch_level.txt'), encoding='utf-8') as f:
                return int(f.read().strip() or '0')
        except Exception:
            return 0

    def get_pending_patch(self):
        """待应用补丁：id 大于本地级别、且 target 匹配当前版本的最新一项。
        仅安装版布局（有入口 stub 的 gui_main pyd）提供补丁应用钩子；
        源码树没有该钩子、无法应用，直接返回 None 避免无效弹窗与下载。"""
        if not os.path.isfile(os.path.join(self._app_root(), 'gui_main.cp313-win_amd64.pyd')):
            return None
        for p in sorted(self.patches, key=lambda x: int(x.get('id', 0)), reverse=True):
            try:
                if p.get('target') == VERSION and int(p.get('id', 0)) > self.get_local_patch_level():
                    return p
            except (TypeError, ValueError):
                continue
        return None

    def download_and_stage_patch(self, patch, progress_cb=None):
        """下载补丁 zip → sha256 校验 → 暂存 config/pending_patch.zip(+.meta)。
        实际覆盖在**下次启动时**由入口 stub 在导入任何业务 pyd 之前原子完成
        （运行中 pyd 被 Windows 锁定，不能热覆盖）。"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36 Edg/112.0.1722.64"
        }
        proxy = self.get_system_proxy()
        proxies = {"http": proxy, "https": proxy}
        cfg_dir = os.path.join(self._app_root(), 'config')
        os.makedirs(cfg_dir, exist_ok=True)
        tmp = os.path.join(cfg_dir, 'pending_patch.zip.part')
        done = os.path.join(cfg_dir, 'pending_patch.zip')
        h = hashlib.sha256()
        with requests.get(patch['url'], headers=headers, timeout=15, stream=True,
                          allow_redirects=True, proxies=proxies) as r:
            r.raise_for_status()
            total = r.headers.get('Content-Length')
            total = int(total) if total else None
            got = 0
            with open(tmp, 'wb') as f:
                for chunk in r.iter_content(1 << 20):
                    if not chunk:
                        continue
                    f.write(chunk)
                    h.update(chunk)
                    got += len(chunk)
                    if progress_cb and total:
                        progress_cb(min(100, int(got * 100 / total)))
        digest = h.hexdigest()
        expect = (patch.get('sha256') or '').lower()
        if expect and digest != expect:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise ValueError('sha256 mismatch: %s != %s' % (digest, expect))
        if os.path.exists(done):
            os.remove(done)
        os.replace(tmp, done)
        with open(done + '.meta', 'w', encoding='utf-8') as f:
            json.dump({'sha256': digest, 'id': int(patch.get('id', 0)),
                       'notes': patch.get('notes', '')}, f, ensure_ascii=False)
        return done

    def get_system_proxy(self):
        """ get system proxy """
        if sys.platform == "win32":
            try:
                import winreg

                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
                    enabled, _ = winreg.QueryValueEx(key, 'ProxyEnable')

                    if enabled:
                        # QueryValueEx 返回 (值, 类型) 元组——必须解包，
                        # 直接拼串会在有系统代理时 TypeError 崩掉整个更新检查
                        server, _ = winreg.QueryValueEx(key, 'ProxyServer')
                        return "http://" + server
            except OSError:
                pass
        elif sys.platform == "darwin":
            s = os.popen('scutil --proxy').read()
            info = dict(re.findall(r'(?m)^\s+([A-Z]\w+)\s+:\s+(\S+)', s))

            if info.get('HTTPEnable') == '1':
                return f"http://{info['HTTPProxy']}:{info['HTTPPort']}"
            elif info.get('ProxyAutoConfigEnable') == '1':
                return info['ProxyAutoConfigURLString']

        return os.environ.get("http_proxy")