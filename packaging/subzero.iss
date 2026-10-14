; 净影 SubZero v1.1.0 安装包脚本
; 打包：pyd 编译发行目录(pyd-dist，源码保护) + 预烘焙运行时(embeddable 3.13.12 + 轻量UI依赖) + SubZero.exe 启动器
; 构建：先 python packaging\build_pyd.py（Cython 编译 backend/ui → pyd-dist）再 ISCC
; 首启自举：gui.py 钩子 → first_run.py（pip 镜像装依赖 → 权重魔搭/GitHub 双源下载）
; 编译: ISCC.exe packaging\subzero.iss

#define MyAppName "净影 SubZero"
#define MyAppVersion "1.1.0"
#define MyAppPublisher "SubZero"
#define MyAppExeName "SubZero.exe"

[Setup]
AppId={{7E1F2A93-6B54-4C7D-9A18-SUBZERO1000}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\SubZero
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=净影SubZero-Setup-{#MyAppVersion}
SetupIconFile=..\design\subzero.ico
UninstallDisplayIcon={app}\design\subzero.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
PrivilegesRequired=admin
MinVersion=10.0

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Dirs]
; 运行时生成用户配置 config\config.json
Name: "{app}\config"

[Files]
; pyd 编译发行目录（build_pyd.py 产物：53 个 .pyd + stub/first_run/scenedetect 保留 .py）
Source: "pyd-dist\gui.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README_en.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "requirements-lock.txt"; DestDir: "{app}\packaging"; Flags: ignoreversion
Source: "pyd-dist\backend\*"; DestDir: "{app}\backend"; Excludes: "models,ffmpeg,__pycache__,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "pyd-dist\ui\*"; DestDir: "{app}\ui"; Excludes: "__pycache__,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\design\*"; DestDir: "{app}\design"; Excludes: "_*"; Flags: ignoreversion recursesubdirs createallsubdirs
; 预烘焙运行时（embeddable + PySide6 等轻量依赖）
Source: "runtime\runtime\*"; DestDir: "{app}\runtime"; Flags: ignoreversion recursesubdirs createallsubdirs
; 安装版启动器
Source: "launcher\SubZero.exe"; DestDir: "{app}"; DestName: "{#MyAppExeName}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
