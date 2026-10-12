param([Parameter(Mandatory=$true)][string]$Payload)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
    $data = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Payload)) | ConvertFrom-Json
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
    [Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
    [Windows.UI.Notifications.ToastNotifier, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
    [Windows.UI.Notifications.NotificationSetting, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] | Out-Null
    $xml = [System.Xml.XmlDocument]::new()
    $root = $xml.CreateElement('toast')
    $xml.AppendChild($root) | Out-Null
    if ($data.url) {
        $root.SetAttribute('activationType', 'protocol')
        $root.SetAttribute('launch', $data.url)
    }
    $visual = $xml.CreateElement('visual')
    $root.AppendChild($visual) | Out-Null
    $binding = $xml.CreateElement('binding')
    $binding.SetAttribute('template', 'ToastGeneric')
    $visual.AppendChild($binding) | Out-Null
    $image = $xml.CreateElement('image')
    $image.SetAttribute('placement', 'appLogoOverride')
    $image.SetAttribute('src', $data.icon)
    $binding.AppendChild($image) | Out-Null
    foreach ($value in @($data.title, $data.body)) {
        $text = $xml.CreateElement('text')
        $text.InnerText = $value
        $binding.AppendChild($text) | Out-Null
    }
    $audio = $xml.CreateElement('audio')
    $audio.SetAttribute('silent', 'true')
    $root.AppendChild($audio) | Out-Null
    $document = [Windows.Data.Xml.Dom.XmlDocument]::new()
    $document.LoadXml($xml.OuterXml)
    $toast = [Windows.UI.Notifications.ToastNotification]::new($document)
    if ($data.tag) { $toast.Tag = $data.tag }
    $toast.Group = $data.group
    $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($data.app_id)
    $notifier.Show($toast)
    # The first Show creates the app's settings record on unpackaged Windows apps.
    $setting = $notifier.get_Setting()
    if ($setting.ToString() -ne 'Enabled') {
        throw ('Windows notifications are disabled: ' + $setting)
    }
    Write-Output 'submitted'
} catch {
    Write-Output $_.Exception.Message
    exit 1
}
