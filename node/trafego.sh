#!/bin/sh
# Gerador de tráfego do Kit NOC (iperf3 UDP): FW -> OPERADORA. (O Kit Provedor usa o lab-provedor.)
#   Download (entra na wan1 = net.if.in.linkprincipal): iperf3 -R (a operadora envia)
#   Upload   (sai da wan1   = net.if.out.linkprincipal): iperf3 normal
# A cada minuto recalcula a taxa pelo perfil do link (msp/links.csv) e pelo cenário
# atual (/tmp/lab-cenario, escrito pelo ./lab.sh: normal | saturar).
# O "dia" do lab dura LAB_DIA_SEGUNDOS (padrão 3600 s = 1 dia a cada hora).
P="${LAB_NET_PREFIX:?}"
DIA="${LAB_DIA_SEGUNDOS:-3600}"
# Kit NOC: FW <-> OPERADORA
CSV=/lab/msp/links.csv; CAMPOS=1,4,5; BASE=5200; LEN=1400

k=0; CONTRATADO=0; PERFIL=normal
for linha in $(grep -v '^#' "$CSV" | grep -v '^[[:space:]]*$' | cut -d';' -f$CAMPOS | tr ' ' '_'); do
  k=$((k + 1))
  if [ "${linha%%;*}" = "$(hostname)" ]; then
    CONTRATADO=$(echo "$linha" | cut -d';' -f2); PERFIL=$(echo "$linha" | cut -d';' -f3); N=$k
  fi
done
[ -n "${N:-}" ] || { echo "trafego: $(hostname) não está em $CSV" >&2; exit 1; }
ALVO="$P.4.2"
PDOWN=$((BASE + 2 * N - 1))
PUP=$((BASE + 2 * N))

taxas() {
  awk -v t="$(date +%s)" -v dia="$DIA" -v c="$CONTRATADO" -v perfil="$PERFIL" -v cen="$1" -v seed="$$$(date +%s)" 'BEGIN {
    srand(seed); pi = 3.14159265; h = (t % dia) / dia * 24
    curva = (h >= 6 && h <= 21) ? sin(pi * (h - 6) / 15) : 0   # 0 de madrugada, 1 às 13h30
    r = rand() * 2 - 1
    if (perfil == "comercial")   u = 0.10 + 0.58 * curva ^ 1.3 + 0.04 * r   # pico de dia ~68%
    else if (perfil == "limite") u = 0.885 + 0.035 * r                      # 85 a 92%
    else if (perfil == "acima")  u = 1.10 + 0.04 * r                        # 106 a 114%
    else if (perfil == "baixo")  u = 0.15 + 0.05 * r                        # 10 a 20%
    else if (perfil == "alto")   u = 0.30 + 0.48 * curva ^ 1.1 + 0.03 * r   # pico de dia ~78%
    else if (perfil == "parado") u = 0.012 + 0.006 * r                      # ~1% (quase parado)
    else                         u = 0.30
    if (cen == "saturar") u = 1.12 + 0.03 * r
    if (u < 0.02) u = 0.02
    down = u * c; up = down * (perfil == "baixo" ? 0.6 : 0.4)
    printf "%dK %dK\n", down * 1000, up * 1000
  }'
}

sentido() { # sentido <down|up>: laço independente para cada direção
  while true; do
    cenario=$(cat /tmp/lab-cenario 2>/dev/null || echo normal)
    set -- $(taxas "$cenario")
    inicio=$(date +%s)
    if [ "$SENTIDO" = down ]; then
      iperf3 -c "$ALVO" -p "$PDOWN" -u -R -b "$1" -l "$LEN" -t 60 >/dev/null 2>&1
    else
      iperf3 -c "$ALVO" -p "$PUP" -u -b "$2" -l "$LEN" -t 60 >/dev/null 2>&1
    fi
    # falhou rápido (link fora, servidor ocupado): tenta de novo em 2 s
    [ $(( $(date +%s) - inicio )) -lt 3 ] && sleep 2
  done
}
SENTIDO=down sentido &
SENTIDO=up sentido &
wait
