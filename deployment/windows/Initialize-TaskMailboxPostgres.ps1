param(
    [Parameter(Mandatory=$true)][string]$Root,
    [Parameter(Mandatory=$true)][ValidateRange(1024,65535)][int]$Port
)
$ErrorActionPreference = 'Stop'
$service = 'TaskMailboxPostgreSQL18'
$bin = Join-Path $Root 'pgsql\bin'
$data = Join-Path $Root 'data'
$private = Join-Path $Root 'provision'
if ((Test-Path $data) -or (Get-Service $service -ErrorAction SilentlyContinue)) {
    throw 'A cluster or service already exists. Inspect it before retrying.'
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw 'The selected port is occupied.'
}
& "$bin\postgres.exe" --version
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL binary failed verification.' }

$secret = Get-Content -Raw -LiteralPath "$private\roles.json" | ConvertFrom-Json
foreach ($password in @($secret.admin, $secret.application, $secret.test)) {
    if ($password -cnotmatch '^[0-9a-f]{64}$') { throw 'Invalid provisioning password format.' }
}
# initdb re-executes under a restricted token where the Administrators SID
# cannot grant access. Grant the caller's own SID access during initialization.
$caller = '*' + [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $Root /grant "${caller}:(OI)(CI)M" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Temporary initialization ACL grant failed.' }

$pwfile = Join-Path $private 'initdb-password'
try {
    Set-Content -NoNewline -Encoding ASCII -LiteralPath $pwfile -Value $secret.admin
    & "$bin\initdb.exe" -D $data -U postgres --auth=scram-sha-256 --encoding=UTF8 --locale=C --data-checksums "--pwfile=$pwfile"
    if ($LASTEXITCODE -ne 0) { throw "initdb failed with exit code $LASTEXITCODE." }
} finally {
    try {
        if (Test-Path -LiteralPath $pwfile) { Remove-Item -LiteralPath $pwfile }
    } finally {
        & icacls.exe $Root /remove:g $caller | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Temporary initialization ACL cleanup failed.' }
    }
}

Copy-Item -LiteralPath "$private\server.crt","$private\server.key" -Destination $data
@"

# Task Mailbox dedicated instance. The VPS forward terminates TCP on loopback.
listen_addresses = '127.0.0.1'
port = $Port
ssl = on
ssl_cert_file = 'server.crt'
ssl_key_file = 'server.key'
ssl_min_protocol_version = 'TLSv1.2'
password_encryption = 'scram-sha-256'
shared_buffers = '64MB'
max_connections = 32
event_source = 'TaskMailboxPostgreSQL18'
logging_collector = on
log_destination = 'stderr'
log_directory = 'log'
log_filename = 'postgresql-%a.log'
log_rotation_age = '1d'
log_rotation_size = 0
log_truncate_on_rotation = on
"@ | Add-Content -LiteralPath "$data\postgresql.conf" -Encoding ASCII
@'
hostssl postgres postgres                 127.0.0.1/32 scram-sha-256
hostssl task_mailbox task_mailbox_app     127.0.0.1/32 scram-sha-256
hostssl task_mailbox_test task_mailbox_test 127.0.0.1/32 scram-sha-256
host    all all                           0.0.0.0/0    reject
host    all all                           ::0/0        reject
'@ | Set-Content -LiteralPath "$data\pg_hba.conf" -Encoding ASCII

# Grant a service-specific virtual account access to the new installation only.
& "$bin\pg_ctl.exe" register -N $service -D $data -S auto -e $service
if ($LASTEXITCODE -ne 0) { throw 'Service registration failed.' }
& sc.exe config $service obj= "NT SERVICE\$service"
if ($LASTEXITCODE -ne 0) { throw 'Configuring the virtual account failed.' }
& icacls.exe $Root /grant "NT SERVICE\${service}:(RX)" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Granting root traversal failed.' }
& icacls.exe "$Root\pgsql" /grant "NT SERVICE\${service}:(OI)(CI)RX" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Granting binary access failed.' }
& icacls.exe $data /grant "NT SERVICE\${service}:(OI)(CI)M" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Granting cluster access failed.' }
Start-Service -Name $service
$env:PGPASSWORD = $secret.admin
$env:PGSSLMODE = 'verify-full'
$env:PGSSLROOTCERT = "$private\root.crt"
$env:PGCONNECT_TIMEOUT = '5'
$env:PGCHANNELBINDING = 'require'
$env:PGGSSENCMODE = 'disable'
try {
    # psql reads these values on stdin. Connection strings and SQL never enter process arguments.
    $sql = @"
CREATE ROLE task_mailbox_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '$($secret.application)';
CREATE ROLE task_mailbox_test LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '$($secret.test)';
CREATE DATABASE task_mailbox OWNER task_mailbox_app;
CREATE DATABASE task_mailbox_test OWNER task_mailbox_test;
REVOKE CONNECT ON DATABASE task_mailbox FROM PUBLIC;
REVOKE CONNECT ON DATABASE task_mailbox_test FROM PUBLIC;
"@
    $sql | & "$bin\psql.exe" -X -q -w -v ON_ERROR_STOP=1 -h 127.0.0.1 -p $Port -U postgres -d postgres -f -
    if ($LASTEXITCODE -ne 0) { throw "Database and role provisioning failed with exit code $LASTEXITCODE." }
} finally {
    Remove-Item Env:PGPASSWORD
}
Get-Service -Name $service | Select-Object Name,Status,StartType
