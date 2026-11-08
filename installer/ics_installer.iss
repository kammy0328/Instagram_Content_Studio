; Inno Setup 6 스크립트 - PyInstaller 결과물(dist\InstagramContentStudio)을 설치 파일로 묶는다.
#define MyAppName "Instagram Content Studio"
#define MyAppVersion "1.2.0"
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
SetupIconFile=..\assets\studio.ico
UninstallDisplayIcon={app}\{#MyAppExe}

[Languages]
; Korean.isl 은 기본 설치에 없을 수 있다. 설치 폴더(compiler:) 또는 이 스크립트 옆에 있을 때만 한국어를 켠다.
#if FileExists(CompilerPath + "Languages\Korean.isl")
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
#elif FileExists(SourcePath + "Korean.isl")
Name: "korean"; MessagesFile: "Korean.isl"
#endif
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕 화면 아이콘 만들기"; Flags: unchecked

[Files]
Source: "..\dist\InstagramContentStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 클라우드 중계(Cloudflare Worker) 소스: 사용자가 직접 배포한다 (README.md 의 설정 순서 참고)
Source: "..\worker\*"; DestDir: "{app}\relay"; Excludes: "node_modules\*,.wrangler\*,test\*,.dev.vars"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{#MyAppName} 실행"; Flags: nowait postinstall skipifsilent
; 앱 안에서 자동 업데이트로 조용히 설치된 경우(/LAUNCH=1) 끝나고 다시 실행한다
Filename: "{app}\{#MyAppExe}"; Flags: nowait; Check: ShouldRelaunch

; 프로젝트 데이터(%APPDATA%\InstagramContentStudio)와 출력 폴더는 제거하지 않는다 (사용자 데이터 보호).

[Code]
function ShouldRelaunch: Boolean;
begin
  Result := ExpandConstant('{param:LAUNCH|0}') = '1';
end;
