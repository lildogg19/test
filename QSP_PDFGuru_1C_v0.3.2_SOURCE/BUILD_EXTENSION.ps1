param(
    [Parameter(Mandatory=$true)][string]$OneCExe,
    [Parameter(Mandatory=$true)][string]$TestBaseDirectory,
    [string]$UserName = ''
)
$ErrorActionPreference = 'Stop'
$extensionName = 'QSPPDF_РаспознаваниеСчетов'
$sourceDirectory = [System.IO.Path]::GetFullPath($PSScriptRoot)
$baseDirectory = (Resolve-Path -LiteralPath $TestBaseDirectory).Path
if (-not (Test-Path -LiteralPath $OneCExe -PathType Leaf)) { throw '1cv8.exe not found.' }
if (-not (Test-Path -LiteralPath (Join-Path $baseDirectory '1Cv8.1CD') -PathType Leaf)) {
    throw 'Specify a FILE TEST COPY of the Kazakhstan accounting database (1Cv8.1CD).'
}
$baseArgs = @('DESIGNER','/F',$baseDirectory)
if ($UserName) { $baseArgs += @('/N',$UserName) }
$logDirectory = Join-Path $sourceDirectory ('build_logs/' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
function Run-Designer {
    param([string[]]$Options,[string]$LogName)
    $taskArgs = $baseArgs + $Options + @('/Out',(Join-Path $logDirectory $LogName))
    & $OneCExe @taskArgs | Out-Host
    if ($LASTEXITCODE -ne 0) { throw ('Designer failed. See '+(Join-Path $logDirectory $LogName)) }
}
Write-Host ('Loading extension into TEST COPY: '+$baseDirectory)
Run-Designer -Options @('/LoadConfigFromFiles',$sourceDirectory,'-Extension',$extensionName) -LogName '01_load.log'
Run-Designer -Options @('/CheckConfig','-Server','-ThinClient','-ExtendedModulesCheck','-Extension',$extensionName) -LogName '02_check.log'
$outputFile = Join-Path $logDirectory 'QSP_PDFGuru_1C_v0.3.2.cfe'
Run-Designer -Options @('/DumpCfg',$outputFile,'-Extension',$extensionName) -LogName '03_export.log'
if (-not (Test-Path -LiteralPath $outputFile -PathType Leaf)) { throw 'CFE file was not created.' }
Write-Host ('Created: '+$outputFile)
Write-Host 'Review ALL logs. A created CFE is not proof of applicability or successful runtime operation.'
Write-Host 'Database configuration was not updated by this script. Test it separately on the copy.'
