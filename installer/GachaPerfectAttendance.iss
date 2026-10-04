; 二遊全勤君 安裝程式（Inno Setup 6）
; 編譯：scripts\build_installer.ps1（會自動帶入版本號）
; 安裝程式本身很小；程式與 Python 環境在安裝時從 GitHub 下載對應版本，全部放在安裝資料夾內。

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
Compression=lzma2
SolidCompression=yes
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

[Files]
Source: "setup-core.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "..\assets\icon.ico"; DestDir: "{app}\assets"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: "-m gachahub"; WorkingDir: "{app}"; IconFilename: "{app}\assets\icon.ico"; Comment: "{#AppName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: "-m gachahub"; WorkingDir: "{app}"; IconFilename: "{app}\assets\icon.ico"; Comment: "{#AppName}"; Tasks: desktopicon

[Run]
Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: "-m gachahub"; WorkingDir: "{app}"; Description: "{cm:LaunchApp}"; Flags: postinstall nowait skipifsilent; Check: CoreSucceeded

[Code]
var
  CoreExitCode: Integer;

function CoreSucceeded(): Boolean;
begin
  Result := CoreExitCode = 0;
end;

function ReadFirstLine(const FileName: string): string;
var
  Lines: TArrayOfString;
begin
  Result := '';
  if LoadStringsFromFile(FileName, Lines) and (GetArrayLength(Lines) > 0) then
    Result := Lines[0];
end;

{ 執行 setup-core.ps1：下載程式、建立 Python 環境。期間顯示目前步驟。 }
procedure RunCore();
var
  Page: TOutputMarqueeProgressWizardPage;
  StatusFile, DoneFile, Params, Line: string;
  ResultCode, Waited: Integer;
begin
  CoreExitCode := -1;
  ForceDirectories(ExpandConstant('{app}\runtime'));
  StatusFile := ExpandConstant('{app}\runtime\setup-status.txt');
  DoneFile := StatusFile + '.done';
  DeleteFile(DoneFile);
  DeleteFile(StatusFile);
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\installer\setup-core.ps1') +
    '" -Root "' + ExpandConstant('{app}') + '" -Tag "v{#AppVer}" -StatusFile "' + StatusFile + '"';
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params, ExpandConstant('{app}'),
              SW_HIDE, ewNoWait, ResultCode) then
  begin
    MsgBox('無法啟動 PowerShell：' + SysErrorMessage(ResultCode), mbError, MB_OK);
    Exit;
  end;
  Page := CreateOutputMarqueeProgressPage('正在下載並設定執行環境',
    '首次安裝需下載 Python 與相依套件（約 300 MB），視網路速度約需數分鐘，請稍候。');
  Page.Show;
  try
    Waited := 0;
    while not FileExists(DoneFile) do
    begin
      Line := ReadFirstLine(StatusFile);
      if Line <> '' then
        Page.SetText(Line, '');
      Page.Animate;
      Sleep(80);
      Waited := Waited + 80;
      if Waited > 60 * 60 * 1000 then
        Break;  { 最多等 60 分鐘 }
    end;
  finally
    Page.Hide;
  end;
  CoreExitCode := StrToIntDef(Trim(ReadFirstLine(DoneFile)), -1);
  if CoreExitCode <> 0 then
    MsgBox('執行環境設定失敗：' + ReadFirstLine(StatusFile) + #13#10#13#10 +
           '請確認網路連線後重新執行安裝程式（設定與紀錄不會遺失）。' + #13#10 +
           '詳細記錄：' + ExpandConstant('{app}\runtime\setup.log'), mbError, MB_OK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RunCore();
end;

{ ---- 解除安裝 ---- }

procedure StopRunningApp();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -ExecutionPolicy Bypass -Command "$r=''' + ExpandConstant('{app}') + '\'';' +
    ' Get-Process pythonw,python -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($r, ''OrdinalIgnoreCase'') } | Stop-Process -Force"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure RemoveWakeTasks();
var
  ResultCode: Integer;
begin
  { 喚醒電腦用的排程工作由程式以系統管理員身分建立，刪除時也需要提權 }
  if Exec(ExpandConstant('{sys}\schtasks.exe'), '/Query /TN "\GachaHub\"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
     and (ResultCode = 0) then
    if MsgBox('要一併移除「喚醒電腦」用的 Windows 排程工作嗎？（需要系統管理員權限）', mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('runas', ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
        '-NoProfile -Command "Get-ScheduledTask -TaskPath ''\GachaHub\'' | Unregister-ScheduledTask -Confirm:$false"',
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
    if DirExists(ExpandConstant('{app}\data')) then
      KeepData := MsgBox('要保留設定、任務鏈與執行紀錄（data 資料夾）嗎？' + #13#10 +
                         '保留的話，之後重新安裝可以直接沿用。', mbConfirmation, MB_YESNO) = IDYES;
    DeleteAppFiles(KeepData);
  end;
end;
