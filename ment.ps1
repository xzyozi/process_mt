# CSVファイルとログファイルのパスを設定
$csvPath = Join-Path -Path $PSScriptRoot -ChildPath "process_schedule.csv"
$logPath = Join-Path -Path $PSScriptRoot -ChildPath "task_log.txt"
$OUTPUT_LOG_FLG = $true  # ログ出力を有効化

# ログ出力関数
function Write-Log {
    param (
        [string]$message
    )
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logMessage = "$timestamp - $message"
    if ($OUTPUT_LOG_FLG) {
        Add-Content -Path $logPath -Value $logMessage
    }
    Write-Output $logMessage
}

# 現在のディレクトリをログに記録
Write-Log "Current Directory: $PSScriptRoot"

# メインループ
while ($true) {
    if (-Not (Test-Path -Path $csvPath)) {
        Write-Log "CSV file not found: $csvPath"
        break
    }

    try {
        $tasks = Import-Csv -Path $csvPath -Encoding Default
    } catch {
        Write-Log "Error reading CSV file: $_"
        Start-Sleep -Seconds 60
        continue
    }

    foreach ($task in $tasks) {
        # 値が空でないことを確認
        if ([string]::IsNullOrWhiteSpace($task.Enabled) -or 
            [string]::IsNullOrWhiteSpace($task.ProcessName) -or 
            [string]::IsNullOrWhiteSpace($task.ExecutablePath) -or 
            [string]::IsNullOrWhiteSpace($task.Frequency)) {
            Write-Log "Skipping task with missing required fields: $($task.ProcessName)"
            continue
        }

        # 値を適切に変換
        $enabled = $false
        if ($task.Enabled -eq $true -or $task.Enabled -eq "true" -or $task.Enabled -eq "1") {
            $enabled = $true
        }
        
        $processName = $task.ProcessName
        $executablePath = $task.ExecutablePath
        $arguments = $task.Arguments
        
        # 数値変換を確実に
        $frequency = 0
        if (-not [int]::TryParse($task.Frequency, [ref]$frequency)) {
            Write-Log "Invalid frequency value for task: $processName - using default of 30 minutes"
            $frequency = 30
        }
        
        # 日時変換を確実に
        $lastRunTime = [datetime]"1900-01-01"
        if (-not [string]::IsNullOrWhiteSpace($task.LastRunTime)) {
            try {
                $lastRunTime = [datetime]::Parse($task.LastRunTime)
            } catch {
                Write-Log "Invalid LastRunTime for task: $processName - using default"
            }
        }

        if ($enabled -and $processName -and $executablePath -and $frequency -gt 0) {
            $nextRunTime = $lastRunTime.AddMinutes($frequency)
            $currentTime = Get-Date

            if ($currentTime -ge $nextRunTime) {
                Write-Log "Running task: $processName"

                # 実行ファイルのパスを絶対パスに変換
                $fullExecutablePath = if ($executablePath -match "^[a-zA-Z]:\\") {
                    $executablePath
                } else {
                    Join-Path -Path $PSScriptRoot -ChildPath $executablePath
                }

                # ファイルの存在を確認
                if (-not (Test-Path -Path $fullExecutablePath)) {
                    Write-Log "Executable file not found: $fullExecutablePath"
                    $task.LastRunTime = $currentTime.ToString("yyyy-MM-dd HH:mm:ss")
                    continue
                }

                # ファイルの拡張子を確認
                $fileExtension = [System.IO.Path]::GetExtension($fullExecutablePath).ToLower()
                
                try {
                    # 拡張子によって適切な実行方法を選択
                    if ($fileExtension -eq ".ps1") {
                        # PowerShellスクリプトを実行
                        Write-Log "Executing PowerShell script: $fullExecutablePath"
                        if ($arguments -and $arguments.Trim() -ne "") {
                            & $fullExecutablePath $arguments.Split(" ")
                        } else {
                            & $fullExecutablePath
                        }
                    } elseif ($fileExtension -eq ".bat" -or $fileExtension -eq ".cmd") {
                        # バッチファイルを実行
                        Write-Log "Executing batch file: $fullExecutablePath"
                        if ($arguments -and $arguments.Trim() -ne "") {
                            cmd.exe /c "$fullExecutablePath $arguments"
                        } else {
                            cmd.exe /c "$fullExecutablePath"
                        }
                    } else {
                        # 通常の実行ファイル
                        Write-Log "Executing application: $fullExecutablePath"
                        if ($arguments -and $arguments.Trim() -ne "") {
                            Start-Process -FilePath $fullExecutablePath -ArgumentList $arguments -NoNewWindow -ErrorAction Stop
                        } else {
                            Start-Process -FilePath $fullExecutablePath -NoNewWindow -ErrorAction Stop
                        }
                    }
                    Write-Log "Task completed: $processName"
                    $task.LastRunTime = $currentTime.ToString("yyyy-MM-dd HH:mm:ss")
                } catch {
                    Write-Log "Task failed: $processName - $_"
                    $task.LastRunTime = $currentTime.ToString("yyyy-MM-dd HH:mm:ss")
                }
            } else {
                Write-Log "Next run time for $processName : $nextRunTime (Current: $currentTime)"
            }
        } else {
            $reason = if (-not $enabled) { "Disabled" } 
                      elseif (-not $processName) { "Missing process name" }
                      elseif (-not $executablePath) { "Missing executable path" }
                      elseif ($frequency -le 0) { "Invalid frequency" }
                      else { "Unknown reason" }
                      
            Write-Log "Skipping task: $processName ($reason)"
        }
    }

    try {
        # CSVファイルを更新
        $tasks | Export-Csv -Path $csvPath -NoTypeInformation -Encoding Default
        Write-Log "Updated CSV file successfully"
    } catch {
        Write-Log "Error updating CSV file: $_"
    }

    # 5分間スリープ
    Write-Log "Sleeping for 5 minutes..."
    Start-Sleep -Seconds 300
}