; 二遊全勤君 安裝程式（Inno Setup 6）
; 編譯：scripts\build_installer.ps1（先以 scripts\build_bundle.py 建立 runtime\build\stage，再帶入版本號編譯）
; 離線安裝：程式、獨立的 Python 3.12 與所有套件都包在安裝檔內，安裝時不需要網路。

#ifndef AppVer
  #define AppVer "0.0.0"
#endif
#define AppName "二遊全勤君"
#define AppDirName "GachaPerfectAttendance"
#define Repo "sunfish1728/GachaPerfectAttendance"

[Setup]
AppId={{B3F0E3A2-6C1D-4E8B-9F2A-5A7C1D2E4F60}
AppName={#AppName}
AppVersion={#AppVer}
AppVerName={#AppName} {#AppVer}
AppPublisher=sunfish1728
AppPublisherURL=https://github.com/{#Repo}
AppSupportURL=https://github.com/{#Repo}/issues
AppUpdatesURL=https://github.com/{#Repo}/releases
DefaultDirName={autopf}\{#AppDirName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DirExistsWarning=no
UsePreviousAppDir=yes
UsePreviousTasks=yes
; 預設裝在使用者自己的資料夾，不需要系統管理員；也可在一開始選擇為所有使用者安裝
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\runtime\installer
OutputBaseFilename=GachaPerfectAttendance-Setup-{#AppVer}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\assets\icon.ico
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
CloseApplications=no
VersionInfoVersion={#AppVer}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} 安裝程式

[Languages]
Name: "zh_tw"; MessagesFile: "ChineseTraditional.isl"

[CustomMessages]
zh_tw.DesktopIcon=建立桌面捷徑
zh_tw.ExtraTasks=其他選項：
zh_tw.LaunchApp=啟動二遊全勤君

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; GroupDescription: "{cm:ExtraTasks}"

[InstallDelete]
; 更新時先清掉舊的程式與執行環境（含 v0.2.0 線上安裝版留下的 .venv、.local 下載快取）；data 與 runtime 保留
Type: filesandordirs; Name: "{app}\src"
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\.local"
Type: filesandordirs; Name: "{app}\installer"
Type: filesandordirs; Name: "{app}\tests"
Type: filesandordirs; Name: "{app}\scripts"
Type: filesandordirs; Name: "{app}\docs"
Type: filesandordirs; Name: "{app}\adapter-repo"
Type: files; Name: "{app}\pyproject.toml"
Type: files; Name: "{app}\.gitignore"
Type: files; Name: "{app}\.gitattributes"

[Files]
Source: "..\runtime\build\stage\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m gachahub"; WorkingDir: "{app}"; IconFilename: "{app}\assets\icon.ico"; Comment: "{#AppName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m gachahub"; WorkingDir: "{app}"; IconFilename: "{app}\assets\icon.ico"; Comment: "{#AppName}"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m gachahub"; WorkingDir: "{app}"; Description: "{cm:LaunchApp}"; Flags: postinstall nowait skipifsilent

[Code]
procedure StopRunningApp();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -ExecutionPolicy Bypass -Command "$r=''' + ExpandConstant('{app}') + '\'';' +
    ' Get-Process pythonw,python -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($r, ''OrdinalIgnoreCase'') } | Stop-Process -Force"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  { 更新時程式檔會被覆蓋，先結束這個資料夾裡正在執行的二遊全勤君 }
  StopRunningApp();
  Result := '';
end;

{ ---- 解除安裝 ---- }

function WakeTaskFilter(): string;
begin
  { 只處理工作資料夾就是這個安裝位置的喚醒工作，不動其他安裝（例如開發版）建立的 }
  Result := 'Get-ScheduledTask -TaskPath ''\GachaHub\'' -ErrorAction SilentlyContinue | Where-Object { $_.Actions[0].WorkingDirectory -eq ''' +
            ExpandConstant('{app}') + ''' }';
end;

procedure RemoveWakeTasks();
var
  ResultCode: Integer;
  PS: string;
begin
  if UninstallSilent() then
    Exit;
  PS := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  { 有符合的工作時結束碼為 0 }
  if Exec(PS, '-NoProfile -Command "if (@(' + WakeTaskFilter() + ').Count -gt 0) { exit 0 } else { exit 1 }"',
          '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then
    if MsgBox('要一併移除這個安裝建立的「喚醒電腦」Windows 排程工作嗎？（需要系統管理員權限）', mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('runas', PS, '-NoProfile -Command "' + WakeTaskFilter() + ' | Unregister-ScheduledTask -Confirm:$false"',
        '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure DeleteAppFiles(KeepData: Boolean);
var
  FindRec: TFindRec;
  Dir, Path: string;
begin
  Dir := ExpandConstant('{app}');
  if FindFirst(Dir + '\*', FindRec) then
  try
    repeat
      if (FindRec.Name <> '.') and (FindRec.Name <> '..') and not (KeepData and (CompareText(FindRec.Name, 'data') = 0)) then
      begin
        Path := Dir + '\' + FindRec.Name;
        if FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0 then
          DelTree(Path, True, True, True)
        else
          DeleteFile(Path);
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
  if not KeepData then
    RemoveDir(Dir);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  KeepData: Boolean;
begin
  if CurUninstallStep = usUninstall then
  begin
    StopRunningApp();
    RemoveWakeTasks();
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    KeepData := True;
    if DirExists(ExpandConstant('{app}\data')) and not UninstallSilent() then
      KeepData := MsgBox('要保留設定、任務鏈與執行紀錄（data 資料夾）嗎？' + #13#10 +
                         '保留的話，之後重新安裝可以直接沿用。', mbConfirmation, MB_YESNO) = IDYES;
    DeleteAppFiles(KeepData);
  end;
end;
