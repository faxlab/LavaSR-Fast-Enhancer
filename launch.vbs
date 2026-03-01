Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.Run Chr(34) & baseDir & "\launch.bat" & Chr(34), 0, False
