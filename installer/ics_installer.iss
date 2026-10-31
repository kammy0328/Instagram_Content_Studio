; Inno Setup 6 스크립트 - PyInstaller 결과물(dist\InstagramContentStudio)을 설치 파일로 묶는다.
#define MyAppName "Instagram Content Studio"
#define MyAppVersion "1.0.0"
#define MyAppExe "InstagramContentStudio.exe"

[Setup]
AppId={{6D0E5C0E-4B5A-4B58-9B0E-1C5C0A7D2F11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\InstagramContentStudio
DefaultGroupName={#MyAppName}
OutputBaseFilename=InstagramContentStudio_Setup
OutputDir=Output
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExe}

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕 화면 아이콘 만들기"; Flags: unchecked

[Files]
Source: "..\dist\InstagramContentStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{#MyAppName} 실행"; Flags: nowait postinstall skipifsilent

; 프로젝트 데이터(%APPDATA%\InstagramContentStudio)와 출력 폴더는 제거하지 않는다 (사용자 데이터 보호).
