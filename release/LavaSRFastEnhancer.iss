; Inno Setup script for LavaSR Fast Enhancer
; Compile with:
;   ISCC.exe /DMyAppVersion=0.1.0 /DSourceDir="...\dist\LavaSRFastEnhancer" /DOutputDir="...\release\out" release\LavaSRFastEnhancer.iss

#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif

#ifndef SourceDir
  #error SourceDir define is required
#endif

#ifndef OutputDir
  #define OutputDir "."
#endif

[Setup]
AppId={{A18CC302-B03E-4DD8-9C8F-7253DBA00C41}
AppName=LavaSR Fast Enhancer
AppVersion={#MyAppVersion}
AppPublisher=QATSISoft
AppPublisherURL=https://github.com/QATSISoft/LavaSR-Fast-Enhancer
DefaultDirName={autopf}\LavaSR Fast Enhancer
DefaultGroupName=LavaSR Fast Enhancer
AllowNoIcons=yes
LicenseFile=..\LICENSE
OutputDir={#OutputDir}
OutputBaseFilename=LavaSR-Fast-Enhancer-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\LavaSRFastEnhancer.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop icon"; GroupDescription: "Additional icons:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\LavaSR Fast Enhancer"; Filename: "{app}\LavaSRFastEnhancer.exe"
Name: "{autodesktop}\LavaSR Fast Enhancer"; Filename: "{app}\LavaSRFastEnhancer.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\LavaSRFastEnhancer.exe"; Description: "Launch LavaSR Fast Enhancer"; Flags: nowait postinstall skipifsilent
