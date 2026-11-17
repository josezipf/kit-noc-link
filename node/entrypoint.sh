#!/bin/sh
# Papel de cada container do laboratório: noc, provedor, fw, operadora.
set -eu
P="${LAB_NET_PREFIX:?LAB_NET_PREFIX ausente}"

if_por_ip() { ip -o -4 addr show | awk -v ip="$1" '{split($4,a,"/"); if (a[1]==ip) {print $2; exit}}'; }

renomear() { # renomear <ip_atual> <novo_nome> [manter_ip]
  dev=$(if_por_ip "$1")
  [ -n "$dev" ] || { echo "Interface com IP $1 não encontrada" >&2; exit 1; }
  [ "${3:-}" = manter_ip ] || ip addr flush dev "$dev"
  ip link set "$dev" down
  ip link set "$dev" name "$2"
  ip link set "$2" up
}

linha_csv() { grep -v '^#' "$1" | grep -v '^[[:space:]]*$' | sed -n "${2}p"; }

snmpd_conf() {
  mkdir -p /tmp/net-snmp
  cat > /tmp/snmpd.conf <<CFG
agentAddress udp:161
rocommunity ${LAB_SNMP_COMMUNITY:?LAB_SNMP_COMMUNITY ausente} default
sysLocation ${LAB_LOCATION:-Laboratorio NOTO TI}
sysContact noc@exemplo.invalid
sysServices 78
pass .1.3.6.1.2.1.31.1.1.1.18 /usr/local/bin/ifalias-pass
CFG
}

iniciar_snmpd() { # snmpd em primeiro plano: se ele morrer, o container reinicia
  touch /tmp/lab-node.ready
  exec snmpd -f -C -c /tmp/snmpd.conf -LE3
}

case "${LAB_ROLE:?LAB_ROLE ausente}" in
  noc)  # namespace de rede compartilhado com o zabbix-server
    # VPNs do Kit NOC: a LAN de cada cliente (lan1 do FW) é alcançada através da wan1 do FW
    grep -v '^#' /lab/msp/vpns.csv 2>/dev/null | grep -v '^[[:space:]]*$' | while IFS=';' read -r _vpn fw lan _resto; do
      suf=$(grep "^$fw;" /lab/msp/links.csv | cut -d';' -f2)
      [ -n "$suf" ] && ip route replace "${lan%.*}.0/24" via "$P.4.$suf" || true
    done
    touch /tmp/lab-node.ready
    exec tail -f /dev/null ;;

  provedor)  # Kit Provedor: borda, upstreams, BNG, switch e OLTs (provedor/topologia.json)
    exec python3 /usr/local/bin/lab-provedor ;;

  fw)   # firewall do cliente MSP: wan1 (link monitorado) + lan1
    renomear "${LAB_IP:?LAB_IP ausente}" wan1 manter_ip
    ip link add lan1 type dummy
    ip addr add "192.168.${LAB_LAN_OCTET:-10}.1/24" dev lan1
    ip link set lan1 up
    snmpd_conf
    echo "interface wan1 6 1000000000" >> /tmp/snmpd.conf   # porta WAN gigabit
    echo normal > /tmp/lab-cenario
    /usr/local/bin/lab-trafego &
    iniciar_snmpd ;;

  operadora)  # "internet": ponta remota do tráfego dos links MSP (2 servidores iperf3 por link)
    n=$(grep -v '^#' /lab/msp/links.csv | grep -c -v '^[[:space:]]*$')
    for k in $(seq 1 "$n"); do
      for porta in $((5200 + 2 * k - 1)) $((5200 + 2 * k)); do
        # servidor fixo; --idle-timeout reinicia o servidor se ele travar (ex.: link caiu no meio do teste)
        ( while true; do iperf3 -s -p "$porta" --idle-timeout 75 >/dev/null 2>&1 || true; sleep 1; done ) &
      done
    done
    touch /tmp/lab-node.ready
    exec tail -f /dev/null ;;

  *) echo "Papel desconhecido: $LAB_ROLE" >&2; exit 1 ;;
esac
