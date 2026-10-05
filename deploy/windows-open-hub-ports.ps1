# Run elevated on the Windows host so Vector on Wi-Fi can reach the WSL hub.
# Opens TCP 8080/7443/8088 and UDP 7500-7502, and portproxies TCP into WSL.
$ErrorActionPreference = 'Stop'
$wsl = '172.27.171.19'
$tcp = 8080, 7443, 8088
$udp = 7500, 7501, 7502

foreach ($p in $tcp) {
  Get-NetFirewallRule -DisplayName "victor-hub-TCP-$p" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
  New-NetFirewallRule -DisplayName "victor-hub-TCP-$p" -Direction Inbound -Action Allow -Protocol TCP -LocalPort $p -Profile Any | Out-Null
  netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$p 2>$null | Out-Null
  netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=$p connectaddress=$wsl connectport=$p | Out-Null
}
foreach ($p in $udp) {
  Get-NetFirewallRule -DisplayName "victor-hub-UDP-$p" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
  New-NetFirewallRule -DisplayName "victor-hub-UDP-$p" -Direction Inbound -Action Allow -Protocol UDP -LocalPort $p -Profile Any | Out-Null
}
Write-Output "victor hub ports open on 0.0.0.0 -> $wsl"
netsh interface portproxy show all
