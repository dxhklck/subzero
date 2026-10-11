# -*- coding: utf-8 -*-
"""
@desc: 关于页面（版权 / 检查更新 / 项目链接）
"""

from PySide6 import QtWidgets, QtCore, QtGui
from qfluentwidgets import (ScrollArea, ExpandLayout, SettingCardGroup, SwitchSettingCard,
                            HyperlinkCard, PrimaryPushSettingCard, MessageBox, InfoBar,
                            FluentIcon)
from backend.config import config, tr, VERSION, PROJECT_HOME_URL, PROJECT_ISSUES_URL, PROJECT_RELEASES_URL, PROJECT_UPDATE_URLS
from backend.tools.version_service import VersionService
from backend.tools.concurrent import TaskExecutor


class AboutInterface(ScrollArea):
    """关于页面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.version_manager = VersionService()
        self.__init_widgets()

    def __init_widgets(self):
        self.scrollWidget = QtWidgets.QWidget(self)
        self.expandLayout = ExpandLayout(self.scrollWidget)

        self.setWidget(self.scrollWidget)
        self.enableTransparentBackground()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setAttribute(QtCore.Qt.WA_StyledBackground)

        self.setup_ui()
        self.setup_layout()

    def setup_ui(self):
        self.about_group = SettingCardGroup(tr["Setting"]["AboutSetting"], self.scrollWidget)

        self.check_update_on_startup = SwitchSettingCard(
            configItem=config.checkUpdateOnStartup,
            icon=FluentIcon.UPDATE,
            title=tr["Setting"]["CheckUpdateOnStartup"],
            content=tr["Setting"]["CheckUpdateOnStartupDesc"],
            parent=self.about_group
        )
        # 版权信息（按钮即检查更新）
        self.copyright = PrimaryPushSettingCard(
            text=tr["Setting"]["CopyrightButton"],
            icon=FluentIcon.MAIL,
            title=tr["Setting"]["CopyrightTitle"],
            content=tr["Setting"]["CopyrightDesc"].format(VERSION),
            parent=self.about_group
        )
        self.copyright.clicked.connect(lambda: self.check_update())
        # 项目链接（仓库地址配置后才显示）
        self.project_link = None
        if PROJECT_HOME_URL:
            self.project_link = HyperlinkCard(
                url=PROJECT_HOME_URL,
                text=PROJECT_HOME_URL,
                icon=FluentIcon.GITHUB,
                title=tr["Setting"]["ProjectLinkTitle"],
                content=tr["Setting"]["ProjectLinkDesc"],
                parent=self.about_group
            )

    def setup_layout(self):
        self.about_group.addSettingCard(self.check_update_on_startup)
        self.about_group.addSettingCard(self.copyright)
        if self.project_link is not None:
            self.about_group.addSettingCard(self.project_link)
        self.expandLayout.addWidget(self.about_group)
        self.expandLayout.setSpacing(16)
        self.expandLayout.setContentsMargins(16, 16, 16, 48)

    def show_message_box(self, title, content, showYesButton=False, yesSlot=None):
        w = MessageBox(title, content, self)
        if not showYesButton:
            w.cancelButton.setText(self.tr('Close'))
            w.yesButton.hide()
            w.buttonLayout.insertStretch(0, 1)
        if w.exec() and yesSlot is not None:
            yesSlot()

    def check_update(self, ignore=False):
        if not PROJECT_UPDATE_URLS:
            if not ignore:
                InfoBar.warning(
                    title=tr["Setting"]["NoUpdatesAvailableTitle"],
                    content=tr["Setting"]["NoUpdatesAvailableDesc"],
                    parent=self, duration=3000)
            return
        TaskExecutor.runTask(self.version_manager.has_new_version).then(
            lambda success: self.on_version_info_fetched(success, ignore))

    def on_version_info_fetched(self, success, ignore=False):
        if success:
            self.show_message_box(
                tr["Setting"]["UpdatesAvailableTitle"],
                tr["Setting"]["UpdatesAvailableDesc"].format(self.version_manager.lastest_version),
                True,
                lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(PROJECT_RELEASES_URL))
            )
        elif not ignore:
            self.show_message_box(
                tr["Setting"]["NoUpdatesAvailableTitle"],
                tr["Setting"]["NoUpdatesAvailableDesc"],
            )
