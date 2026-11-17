#!/bin/sh
# Entrega IF-MIB::ifAlias (1.3.6.1.2.1.31.1.1.1.18) lendo /sys/class/net/<if>/ifalias.
# O net-snmp 5.9 do Linux não lê o alias gravado com "ip link set <if> alias",
# então este script (diretiva "pass" do snmpd) faz essa ponte.
# Em equipamento real (MikroTik, Huawei, Juniper, Cisco...) isso já é nativo.
BASE=.1.3.6.1.2.1.31.1.1.1.18

lista() {
  for d in /sys/class/net/*; do
    printf '%s %s\n' "$(cat "$d/ifindex")" "$(cat "$d/ifalias" 2>/dev/null)"
  done | sort -n
}

case "${1:-}" in
  -g)
    idx=${2#"$BASE".}
    case "$idx" in ''|*[!0-9]*) exit 0 ;; esac
    lista | while read -r i alias; do
      if [ "$i" = "$idx" ]; then printf '%s\nstring\n%s\n' "$BASE.$i" "$alias"; break; fi
    done ;;
  -n)
    if [ "$2" = "$BASE" ]; then idx=-1; else idx=${2#"$BASE".}; idx=${idx%%.*}; fi
    case "$idx" in -1) ;; ''|*[!0-9]*) exit 0 ;; esac
    lista | while read -r i alias; do
      if [ "$i" -gt "$idx" ]; then printf '%s\nstring\n%s\n' "$BASE.$i" "$alias"; break; fi
    done ;;
  -s) echo not-writable ;;
esac
