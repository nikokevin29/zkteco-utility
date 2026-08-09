; ZKTeco Utility — Windows installer (Inno Setup)
; Build: ISCC installer.iss   (needs dist\ZKTeco_Utility.exe built first)
#define AppVersion "5.0.3"

[Setup]
AppId=ZKTecoUtilityCVRAJ
AppName=ZKTeco Utility
AppVersion={#AppVersion}
AppPublisher=CV RAJ
DefaultDirName={localappdata}\ZKTeco Utility
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=ZKTeco_Utility_Setup
SetupIconFile=app_icon.ico
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\ZKTeco_Utility.exe
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Buat shortcut di Desktop"; GroupDescription: "Shortcut:"

[Files]
Source: "dist\ZKTeco_Utility.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\ZKTeco Utility"; Filename: "{app}\ZKTeco_Utility.exe"
Name: "{userdesktop}\ZKTeco Utility"; Filename: "{app}\ZKTeco_Utility.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ZKTeco_Utility.exe"; Description: "Jalankan ZKTeco Utility"; Flags: nowait postinstall skipifsilent

; Data (config.json, absensi.db) sengaja TIDAK dihapus saat uninstall —
; data absensi berharga; hapus manual dari {localappdata}\ZKTeco Utility kalau perlu.
