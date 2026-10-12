param([Parameter(Mandatory=$true)][string]$Payload)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
    $data = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Payload)) | ConvertFrom-Json
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class DesktopShortcut {
    [StructLayout(LayoutKind.Sequential)] public struct Key { public Guid format; public uint id; }
    [StructLayout(LayoutKind.Explicit)] public struct Variant {
        [FieldOffset(0)] public ushort type;
        [FieldOffset(8)] public IntPtr value;
    }
    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface Store {
        void GetCount(out uint count);
        void GetAt(uint index, out Key key);
        void GetValue(ref Key key, out Variant value);
        void SetValue(ref Key key, ref Variant value);
        void Commit();
    }
    [DllImport("shell32.dll", CharSet=CharSet.Unicode, PreserveSig=false)]
    static extern void SHGetPropertyStoreFromParsingName(string path, IntPtr context, uint flags, ref Guid iid, out Store store);
    public static void SetAppId(string path, string appId) {
        Guid iid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
        Store store;
        SHGetPropertyStoreFromParsingName(path, IntPtr.Zero, 2, ref iid, out store);
        var key = new Key { format = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), id = 5 };
        var value = new Variant { type = 31, value = Marshal.StringToCoTaskMemUni(appId) };
        try { store.SetValue(ref key, ref value); store.Commit(); }
        finally { Marshal.FreeCoTaskMem(value.value); Marshal.ReleaseComObject(store); }
    }
}
'@
    $programs = [Environment]::GetFolderPath('Programs')
    $shortcutPath = Join-Path $programs 'Xianyu AutoAgent.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $data.python
    $shortcut.Arguments = '-m src.services.windows_desktop_launcher'
    $shortcut.WorkingDirectory = $data.directory
    $shortcut.Description = $data.name
    $shortcut.Save()
    [DesktopShortcut]::SetAppId($shortcutPath, $data.app_id)
    Write-Output 'registered'
} catch {
    Write-Output $_.Exception.Message
    exit 1
}
