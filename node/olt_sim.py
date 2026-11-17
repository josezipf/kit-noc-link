#!/usr/bin/env python3
"""OLT simulada do laboratório: responde a árvore .1.3.6.1.4.1.32473.1 (número de empresa de documentação,
RFC 5612) pelo protocolo pass_persist do net-snmp. É a "MIB" que o template NOTO - OLT Lab lê.

  .1.1.0 CPU (%) · .1.2.0 memória (%) · .1.3.0 temperatura (°C)
  .2.1.<col>.<pon>        1 nome · 2 status (1 up, 2 down) · 3 octetos download · 4 octetos upload · 5 ONUs online
  .3.1.<col>.<pon>.<onu>  1 nome · 2 status (padrão NOTO) · 3 RX (centésimos de dBm) · 4 TX · 5 temperatura · 6 distância (m)
                          7 octetos download · 8 octetos upload · 9 número de série · 10 motivo da última queda

Cenários (escritos pelo ./lab.sh em /tmp/olt-cenario.json): {"onus": {"1.3": "los"}, "pons": {"2": "down"}}
"""
import json
import math
import os
import random
import sys
import time
import unicodedata
from pathlib import Path

BASE = (1, 3, 6, 1, 4, 1, 32473, 1)
EU = os.environ["LAB_EQUIP"]
DIA = int(os.environ.get("LAB_DIA_SEGUNDOS", "3600"))
CFG = json.loads(Path("/lab/provedor/topologia.json").read_text(encoding="utf-8"))["equipamentos"][EU]["olt"]
CENARIO = Path("/tmp/olt-cenario.json")
STATUS = {"online": 1, "los": 2, "energia": 3, "offline": 4, "auth": 5, "desativada": 6}
MOTIVO = {"los": "LOS (perda de sinal)", "energia": "dying-gasp (falta de energia)", "offline": "sem resposta",
          "auth": "falha de autenticação", "desativada": "desativada pelo operador"}
SEM_LEITURA = 2147483647  # o que muitas OLTs devolvem quando não há sinal

NOMES = ["Ana Souza", "Bruno Lima", "Carla Dias", "Diego Rocha", "Elisa Prado", "Fábio Nunes", "Gisele Moura", "Hugo Pires",
         "Iara Campos", "João Teles", "Karen Vidal", "Lucas Brito", "Marina Sá", "Nelson Faria", "Olívia Reis", "Paulo Mota",
         "Quésia Lobo", "Rafael Couto", "Sônia Barros", "Tiago Melo", "Úrsula Neves", "Vítor Assis", "Wanda Luz", "Xavier Cruz",
         "Yara Leite", "Zeca Antunes", "Padaria Pão Bom", "Mercado Bom Preço", "Clínica Vida", "Escola Aprender",
         "Oficina do Zé", "Pet Shop Amigo", "Farmácia Saúde", "Academia Forte", "Loja Bela Moda", "Pizzaria Forno",
         "Restaurante Sabor", "Hotel Central", "Posto Avenida", "Ótica Visão"]

# descrição de ONU sem acento, como nas OLTs reais (o net-snmp entrega texto com acento como hexadecimal)
NOMES = [unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode() for n in NOMES]
rnd = random.Random(CFG["semente"])
ONUS = []
nomes = rnd.sample(NOMES, min(len(NOMES), CFG["pons"] * CFG["onus_por_pon"])) * 3
for pon in range(1, CFG["pons"] + 1):
    for onu in range(1, CFG["onus_por_pon"] + 1):
        ONUS.append({"pon": pon, "onu": onu, "nome": nomes.pop(0), "sn": "NOTO%08X" % rnd.getrandbits(32),
                     "rx": round(rnd.uniform(-24.0, -16.0), 2), "tx": round(rnd.uniform(1.8, 2.6), 2),
                     "temp": rnd.randint(38, 52), "dist": rnd.randint(300, 6500), "peso": rnd.uniform(0.3, 1.7)})
PESO_TOTAL = sum(o["peso"] for o in ONUS)
CONT = {}          # contadores de octetos por ONU
ULTIMO = [time.time()]


def curva(t):
    h = (t % DIA) / DIA * 24
    return 0.25 + 0.75 * ((1 + math.cos(2 * math.pi * (h - 21) / 24)) / 2) ** 1.6


def cenario():
    try:
        c = json.loads(CENARIO.read_text())
    except (OSError, ValueError):
        c = {}
    onus = {k: {"status": v["status"]} if "status" in v else {"rx": v["rx"]} for k, v in CFG.get("anomalias", {}).items()}
    for k, v in c.get("onus", {}).items():
        onus[k] = {"rx": -26.5} if v == "rx-baixo" else {"rx": -28.2} if v == "rx-critico" else ({} if v == "normal" else {"status": v})
    return onus, {k for k, v in c.get("pons", {}).items() if v == "down"}


def estado():
    """Atualiza contadores e devolve o estado atual de PONs e ONUs."""
    agora = time.time()
    dt = max(0.0, agora - ULTIMO[0])
    ULTIMO[0] = agora
    onus_cen, pons_down = cenario()
    total = CFG["pico_mbps"] * 1e6 * curva(agora)
    soma = [0.0, 0.0]
    lista = []
    for o in ONUS:
        k = f"{o['pon']}.{o['onu']}"
        c = onus_cen.get(k, {})
        st = "los" if str(o["pon"]) in pons_down else c.get("status", "online")
        rx = c.get("rx", o["rx"] + random.uniform(-0.15, 0.15))
        on = st == "online"
        down = total * o["peso"] / PESO_TOTAL * random.uniform(0.8, 1.2) if on else 0.0
        up = down * 0.15
        cd, cu = CONT.get(k, (rnd.randint(10**9, 10**11), rnd.randint(10**8, 10**10)))
        cd, cu = cd + int(down * dt / 8), cu + int(up * dt / 8)
        CONT[k] = (cd, cu)
        soma[0] += down
        soma[1] += up
        lista.append(dict(o, status=STATUS[st], motivo=MOTIVO.get(st, "") if not on else "", rx=rx if on else None,
                          tx=o["tx"] if on else None, oct_down=cd, oct_up=cu))
    tmp = Path("/tmp/olt-taxa.tmp")
    tmp.write_text(f"{soma[0]:.0f} {soma[1]:.0f}\n")
    tmp.replace("/tmp/olt-taxa")
    return lista, pons_down, agora


def tabela():
    onus, pons_down, agora = estado()
    linhas = [((1, 1, 0), "gauge", int(18 + 14 * curva(agora) + random.uniform(-3, 3))),
              ((1, 2, 0), "gauge", int(41 + random.uniform(-2, 2))),
              ((1, 3, 0), "gauge", int(43 + 4 * curva(agora)))]
    for pon in range(1, CFG["pons"] + 1):
        dela = [o for o in onus if o["pon"] == pon]
        linhas += [((2, 1, 1, pon), "string", f"PON 0/1/{pon}"),
                   ((2, 1, 2, pon), "integer", 2 if str(pon) in pons_down else 1),
                   ((2, 1, 3, pon), "counter64", sum(o["oct_down"] for o in dela) % 2**64),
                   ((2, 1, 4, pon), "counter64", sum(o["oct_up"] for o in dela) % 2**64),
                   ((2, 1, 5, pon), "gauge", sum(1 for o in dela if o["status"] == 1))]
    for o in onus:
        i = (o["pon"], o["onu"])
        cent = lambda v: SEM_LEITURA if v is None else int(round(v * 100))
        linhas += [((3, 1, 1) + i, "string", o["nome"]), ((3, 1, 2) + i, "integer", o["status"]),
                   ((3, 1, 3) + i, "integer", cent(o["rx"])), ((3, 1, 4) + i, "integer", cent(o["tx"])),
                   ((3, 1, 5) + i, "integer", o["temp"]), ((3, 1, 6) + i, "gauge", o["dist"]),
                   ((3, 1, 7) + i, "counter64", o["oct_down"] % 2**64), ((3, 1, 8) + i, "counter64", o["oct_up"] % 2**64),
                   ((3, 1, 9) + i, "string", o["sn"]), ((3, 1, 10) + i, "string", o["motivo"])]
    return sorted((BASE + sufixo, tipo, valor) for sufixo, tipo, valor in linhas)


def oid(texto):
    return tuple(int(x) for x in texto.strip().strip(".").split(".") if x)


def responde(item):
    if item is None:
        print("NONE")
    else:
        o, tipo, valor = item
        print("." + ".".join(map(str, o)))
        print(tipo)
        print(valor)
    sys.stdout.flush()


def main():
    cache = (0.0, [])
    while True:
        linha = sys.stdin.readline()
        if not linha:
            return
        cmd = linha.strip().lower()
        if cmd == "ping":
            print("PONG")
            sys.stdout.flush()
            continue
        if cmd in ("get", "getnext"):
            alvo = oid(sys.stdin.readline())
            if time.time() - cache[0] > 2:   # um walk inteiro usa a mesma foto dos valores
                cache = (time.time(), tabela())
            itens = cache[1]
            if cmd == "get":
                responde(next((x for x in itens if x[0] == alvo), None))
            else:
                responde(next((x for x in itens if x[0] > alvo), None))
        elif cmd == "set":
            sys.stdin.readline()
            sys.stdin.readline()
            print("not-writable")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
