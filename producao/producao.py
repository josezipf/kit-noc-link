#!/usr/bin/env python3
"""NOTO TI - Monitoramento em produção (Kit NOC e Kit Provedor).

Sobe Zabbix 7.0 LTS + PostgreSQL + Grafana em containers e deixa tudo pronto para cadastrar os equipamentos reais:
templates NOTO importados, grupos de hosts, macro da community SNMP, Grafana ligado ao Zabbix e dashboards importados.
Nada do laboratório (equipamentos simulados) entra aqui.

  ./producao.sh up --provedor --msp      instala/atualiza a configuração (pode rodar de novo sem perder nada)
  ./producao.sh status | creds | logs
  ./producao.sh backup | restore <arquivo> | update | down | destroy
"""
import argparse
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
ENV = AQUI / ".env"
BACKUPS = AQUI / "backups"
PROJETO = "nototi-producao"
KITS = ("msp", "provedor")
NOME_KIT = {"msp": "Kit NOC (MSP)", "provedor": "Kit Provedor"}

TPL_MSP = RAIZ / "msp" / "templates" / "template_internet_vpn.yaml"     # NOTO - LINK INTERNET e NOTO - VPN (originais)
TPL_MSP_MD5 = "75a6c0f0af7cd15530cf340ddd9ba304"
TPL_PROVEDOR = RAIZ / "Templates" / "TODOS - Kit Provedor.yaml"         # pacote do Kit Provedor (entregue à parte)
DASHBOARDS = RAIZ / "Dashboards"                                        # pacote de dashboards (entregue à parte)
PREFIXO_DASH = {"msp": "Kit NOC", "provedor": "Kit Provedor"}

GRUPOS = {"msp": ["MSP/Links de internet", "MSP/VPNs"],
          "provedor": ["Provedor/Rede", "Provedor/OLTs", "Provedor/Servidores"]}
GRAFANA_USUARIO = "grafana"
GRAFANA_GRUPO = "NOTO TI - Grafana (somente leitura)"
GRAFANA_PAPEL = "NOTO TI - Grafana (somente leitura)"
TPL_SAUDE_NOTO = "NOTO TI - Saude do Zabbix server"
CONFIRMA = "APAGAR"


class Erro(RuntimeError):
    pass


def diz(texto=""):
    print(texto, flush=True)


# ---------------------------------------------------------------- docker / .env

def roda(args, capturar=False, checar=True, entrada=None, saida=None, timeout=None):
    r = subprocess.run(args, cwd=AQUI, text=saida is None and not isinstance(entrada, bytes),
                       capture_output=capturar and saida is None, input=entrada, stdout=saida, timeout=timeout)
    if checar and r.returncode:
        detalhe = ((r.stderr or r.stdout or "") if capturar and saida is None else "").strip()
        raise Erro(f"Falhou: {' '.join(map(str, args[:8]))}. {detalhe}")
    return r


def compose(*args, **kw):
    return roda(["docker", "compose", "--env-file", str(ENV), "-p", PROJETO, "-f", str(AQUI / "compose.yaml"), *args], **kw)


def exige_docker():
    if not shutil.which("docker"):
        raise Erro("Docker não encontrado. Instale o Docker Engine e o plugin Docker Compose.")
    if roda(["docker", "info"], capturar=True, checar=False).returncode:
        raise Erro("Sem acesso ao Docker. Inicie o serviço e use um usuário do grupo docker.")
    if roda(["docker", "compose", "version"], capturar=True, checar=False).returncode:
        raise Erro("O plugin Docker Compose não está instalado (comando docker compose).")


def le_env():
    if not ENV.exists():
        raise Erro("Ainda não instalado. Execute ./producao.sh up --provedor e/ou --msp.")
    return dict(l.split("=", 1) for l in ENV.read_text().splitlines() if l and not l.startswith("#") and "=" in l)


def grava_env(valores):
    linhas = ["# Gerado pelo ./producao.sh. GUARDE ESTE ARQUIVO: são as senhas desta instalação.",
              *(f"{k}={v}" for k, v in valores.items())]
    tmp = ENV.with_suffix(".tmp")
    with contextlib.suppress(FileNotFoundError):
        tmp.unlink()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(linhas) + "\n")
    tmp.replace(ENV)


def porta_livre(preferida):
    for porta in range(preferida, preferida + 100):
        with socket.socket() as s:
            try:
                s.bind(("0.0.0.0", porta))
                return porta
            except OSError:
                continue
    raise Erro(f"Nenhuma porta livre a partir de {preferida}.")


def inicializa(kits):
    if ENV.exists():
        env = le_env()
        atuais = [k for k in env.get("PROD_KITS", "").split(",") if k]
        novos = sorted(set(atuais) | set(kits), key=KITS.index)
        if novos != atuais:
            env["PROD_KITS"] = ",".join(novos)
            grava_env(env)
        return env
    if roda(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={PROJETO}"], capturar=True).stdout.strip():
        raise Erro(f"Já existe uma instalação {PROJETO} sem o .env desta pasta. Restaure o .env (ou um backup) antes de continuar.")
    env = {
        "PROD_KITS": ",".join(kits),
        "PROD_BIND": os.environ.get("PROD_BIND", "0.0.0.0"),
        "PROD_ZBX_PORT": str(porta_livre(int(os.environ.get("PROD_ZBX_PORT", "8080")))),
        "PROD_GRAFANA_PORT": str(porta_livre(int(os.environ.get("PROD_GRAFANA_PORT", "3000")))),
        "PROD_ZBX_SERVER_PORT": os.environ.get("PROD_ZBX_SERVER_PORT", "10051"),
        "PROD_REDE": os.environ.get("PROD_REDE", "198.18.250.0/24"),
        "PROD_TZ": os.environ.get("PROD_TZ", "America/Sao_Paulo"),
        "PROD_NOME": os.environ.get("PROD_NOME", "NOTO TI - Monitoramento"),
        "PROD_ZABBIX_TAG": "alpine-7.0-latest",
        "PROD_GRAFANA_TAG": "12.3.0",
        "PROD_DB_PASSWORD": secrets.token_hex(24),
        "PROD_ADMIN_PASSWORD": "Noto7!" + secrets.token_urlsafe(14),
        "PROD_GRAFANA_PASSWORD": "Noto7!" + secrets.token_urlsafe(14),
        "PROD_ZBX_GRAFANA_PASSWORD": secrets.token_urlsafe(28),
    }
    grava_env(env)
    diz(f"Configuração criada em {ENV.relative_to(RAIZ)} (senhas novas, permissão 600).")
    return env


def kits_ativos(env):
    return [k for k in env.get("PROD_KITS", "").split(",") if k in KITS]


def endereco_local(env):
    bind = env.get("PROD_BIND", "0.0.0.0")
    return "127.0.0.1" if bind in ("0.0.0.0", "127.0.0.1", "") else bind


def ip_da_maquina():
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    return "IP-deste-servidor"


def confirma(o_que):
    diz()
    diz("  ATENÇÃO: " + o_que)
    diz("  Os dados apagados NÃO poderão ser recuperados (a não ser por um backup feito antes).")
    diz()
    if not sys.stdin.isatty():
        diz("  (sem terminal interativo: lendo a confirmação da entrada padrão)")
    try:
        resposta = input(f"  Tem certeza? Digite {CONFIRMA} para confirmar ou Enter para cancelar: ").strip()
    except EOFError:
        resposta = ""
    if resposta != CONFIRMA:
        raise Erro("Cancelado. Nada foi apagado.")


# ---------------------------------------------------------------- Zabbix API

class Zabbix:
    def __init__(self, env):
        self.url = f"http://{endereco_local(env)}:{env['PROD_ZBX_PORT']}/api_jsonrpc.php"
        self.env = env
        self.token = None
        self.n = 0

    def call(self, metodo, params=None, auth=True):
        self.n += 1
        cab = {"Content-Type": "application/json-rpc"}
        if auth:
            cab["Authorization"] = f"Bearer {self.token}"
        corpo = json.dumps({"jsonrpc": "2.0", "id": self.n, "method": metodo, "params": {} if params is None else params}).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url, corpo, cab), timeout=120) as r:
                res = json.load(r)
        except (OSError, ValueError) as e:
            raise Erro(f"API do Zabbix indisponível em {self.url}: {type(e).__name__}") from e
        if "error" in res:
            raise Erro(f"API {metodo}: {res['error'].get('message', '')} {res['error'].get('data', '')}")
        return res["result"]

    def login(self, senha):
        self.token = self.call("user.login", {"username": "Admin", "password": senha}, auth=False)

    def um(self, metodo, filtro, campo):
        r = self.call(metodo, {"output": [campo], "filter": filtro})
        return r[0][campo] if r else None


def espera_zabbix(env):
    api = Zabbix(env)
    limite, ultimo = time.monotonic() + 900, 0
    while True:
        try:
            versao = api.call("apiinfo.version", auth=False)
            break
        except Erro:
            if time.monotonic() > limite:
                raise Erro("O Zabbix não respondeu em 15 minutos. Veja ./producao.sh logs zabbix-server.")
            if time.monotonic() - ultimo > 20:
                diz("  aguardando o Zabbix criar o banco e subir (na primeira vez leva alguns minutos)...")
                ultimo = time.monotonic()
            time.sleep(3)
    if not versao.startswith("7.0."):
        raise Erro(f"Os templates NOTO TI são para Zabbix 7.0 LTS; encontrado {versao}.")
    try:
        api.login(env["PROD_ADMIN_PASSWORD"])
    except Erro:
        try:
            api.login("zabbix")   # primeira vez: troca a senha padrão do Admin
        except Erro as e:
            raise Erro("Não consegui entrar no Zabbix com a senha do .env nem com a padrão. "
                       "Se você trocou a senha do Admin pela tela, atualize PROD_ADMIN_PASSWORD no .env.") from e
        admin = api.call("user.get", {"output": ["userid"], "filter": {"username": ["Admin"]}})[0]["userid"]
        api.call("user.update", {"userid": admin, "current_passwd": "zabbix", "passwd": env["PROD_ADMIN_PASSWORD"]})
        api.login(env["PROD_ADMIN_PASSWORD"])
        diz("  senha padrão do Admin trocada pela senha do .env")
    return api, versao


REGRAS = {"template_groups": {"createMissing": True, "updateExisting": True},
          "templates": {"createMissing": True, "updateExisting": True},
          "items": {"createMissing": True, "updateExisting": True, "deleteMissing": True},
          "triggers": {"createMissing": True, "updateExisting": True, "deleteMissing": True},
          "discoveryRules": {"createMissing": True, "updateExisting": True, "deleteMissing": True},
          "graphs": {"createMissing": True, "updateExisting": True, "deleteMissing": True},
          "templateDashboards": {"createMissing": True, "updateExisting": True},
          "valueMaps": {"createMissing": True, "updateExisting": True},
          "templateLinkage": {"createMissing": True}}


def renomeia_por_uuid(api, yaml_texto):
    """Template que já existe com outro nome (mesmo UUID) é renomeado antes do import:
    o Zabbix valida as dependências da cascata pelo nome do template."""
    novos = re.findall(r"^  - uuid: '?([0-9a-f]{32})'?\n    template: (.+)\n    name: (.+)$", yaml_texto, re.M)
    atuais = {t["uuid"]: t for t in api.call("template.get", {"output": ["templateid", "uuid", "host", "name"]})} if novos else {}
    for uuid, tecnico, visivel in novos:
        t = atuais.get(uuid)
        tecnico, visivel = tecnico.strip("'\""), visivel.strip("'\"")
        if t and (t["host"], t["name"]) != (tecnico, visivel):
            api.call("template.update", {"templateid": t["templateid"], "host": tecnico, "name": visivel})
            diz(f"  template renomeado: {t['name']} -> {visivel}")


def verifica_arquivos(kits):
    if "msp" in kits:
        if not TPL_MSP.exists():
            raise Erro(f"Não achei {TPL_MSP.relative_to(RAIZ)}. Use a pasta completa do repositório kit-noc-link.")
        if hashlib.md5(TPL_MSP.read_bytes()).hexdigest() != TPL_MSP_MD5:
            raise Erro(f"{TPL_MSP.name} foi alterado. Use o arquivo original do kit.")
    if "provedor" in kits and not TPL_PROVEDOR.exists():
        raise Erro(f"Não achei {TPL_PROVEDOR.relative_to(RAIZ)}. Copie a pasta Templates/ do Kit Provedor para "
                   f"{RAIZ} antes do ./producao.sh up --provedor.")


def importa_templates(api, kits):
    if "msp" in kits:
        api.call("configuration.import", {"format": "yaml", "rules": REGRAS, "source": TPL_MSP.read_text(encoding="utf-8")})
        diz("  OK  templates do Kit NOC: NOTO - LINK INTERNET, NOTO - VPN")
    if "provedor" in kits:
        texto = TPL_PROVEDOR.read_text(encoding="utf-8")
        renomeia_por_uuid(api, texto)
        api.call("configuration.import", {"format": "yaml", "source": texto,
                                          "rules": dict(REGRAS, templateLinkage={"createMissing": True, "deleteMissing": True})})
        n = len(re.findall(r"^    template: ", texto, re.M))
        diz(f"  OK  templates do Kit Provedor: {n} templates NOTO TI (TODOS - Kit Provedor.yaml)")


def configura_zabbix(api, env, kits, community):
    for kit in kits:
        for g in GRUPOS[kit]:
            if not api.um("hostgroup.get", {"name": [g]}, "groupid"):
                api.call("hostgroup.create", {"name": g})
    diz(f"  OK  grupos de hosts: {', '.join(g for k in kits for g in GRUPOS[k])}")

    macro = api.call("usermacro.get", {"globalmacro": True, "output": ["globalmacroid", "value"], "filter": {"macro": "{$SNMP_COMMUNITY}"}})
    if not macro:
        api.call("usermacro.createglobal", {"macro": "{$SNMP_COMMUNITY}", "value": community or "public",
                                            "description": "Community SNMP dos equipamentos (os templates NOTO TI usam esta macro)."})
        diz(f"  OK  macro global {{$SNMP_COMMUNITY}} = {community or 'public'}"
            + ("" if community else "  <- troque em Administração → Macros (ou ./producao.sh up --community X)"))
    elif community and macro[0]["value"] != community:
        api.call("usermacro.updateglobal", {"globalmacroid": macro[0]["globalmacroid"], "value": community})
        diz(f"  OK  macro global {{$SNMP_COMMUNITY}} atualizada para {community}")

    # Grafana: usuário só de leitura pela API. Papel do tipo Super admin (enxerga todos os grupos, inclusive os que
    # você criar depois) mas limitado a métodos *.get e sem acesso à tela do Zabbix.
    regras = {"ui.default_access": 0, "modules.default_access": 0, "actions.default_access": 0,
              "api.access": 1, "api.mode": 1, "api": ["*.get"]}
    papel = api.um("role.get", {"name": [GRAFANA_PAPEL]}, "roleid")
    if papel:
        api.call("role.update", {"roleid": papel, "rules": regras})
    else:
        papel = api.call("role.create", {"name": GRAFANA_PAPEL, "type": 3, "rules": regras})["roleids"][0]
    grupo = api.um("usergroup.get", {"name": [GRAFANA_GRUPO]}, "usrgrpid") or \
        api.call("usergroup.create", {"name": GRAFANA_GRUPO, "gui_access": 3})["usrgrpids"][0]
    usuario = api.um("user.get", {"username": [GRAFANA_USUARIO]}, "userid")
    dados = {"username": GRAFANA_USUARIO, "name": "Grafana", "surname": "(somente leitura)", "roleid": papel,
             "usrgrps": [{"usrgrpid": grupo}], "passwd": env["PROD_ZBX_GRAFANA_PASSWORD"]}
    if usuario:
        api.call("user.update", {"userid": usuario, **dados})
    else:
        api.call("user.create", dados)
    diz("  OK  usuário 'grafana' no Zabbix (somente leitura, usado pelo datasource do Grafana)")

    # host "Zabbix server": o container não tem agente (sai o "Linux by Zabbix agent", que só daria alerta falso);
    # com o Kit Provedor, a saúde do Zabbix passa a ser o template NOTO TI (mesmas chaves do oficial, em português)
    zbx = api.call("host.get", {"output": ["hostid"], "filter": {"host": ["Zabbix server"]}, "selectParentTemplates": ["templateid", "host"],
                                "selectInterfaces": ["interfaceid", "type"]})
    if zbx:
        h = zbx[0]
        noto = api.um("template.get", {"host": [TPL_SAUDE_NOTO]}, "templateid") if "provedor" in kits else None
        tirar = ["Linux by Zabbix agent"] + (["Zabbix server health"] if noto else [])
        sai = [t["templateid"] for t in h["parentTemplates"] if t["host"] in tirar]
        if sai:
            api.call("host.update", {"hostid": h["hostid"], "templates_clear": [{"templateid": t} for t in sai]})
        if noto and noto not in [t["templateid"] for t in h["parentTemplates"]]:
            api.call("host.massadd", {"hosts": [{"hostid": h["hostid"]}], "templates": [{"templateid": noto}]})
        diz("  OK  host 'Zabbix server': " + ("saúde pelo NOTO TI - Saude do Zabbix server" if noto else "saúde pelo template oficial"))


# ---------------------------------------------------------------- Grafana

def grafana(env, metodo, caminho, corpo=None):
    url = f"http://{endereco_local(env)}:{env['PROD_GRAFANA_PORT']}{caminho}"
    cab = {"Content-Type": "application/json",
           "Authorization": "Basic " + base64.b64encode(f"admin:{env['PROD_GRAFANA_PASSWORD']}".encode()).decode()}
    req = urllib.request.Request(url, None if corpo is None else json.dumps(corpo).encode(), cab, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        with contextlib.suppress(ValueError):
            return e.code, json.load(e)
        return e.code, {}
    except (OSError, ValueError):
        return 0, {}


def espera_grafana(env):
    limite = time.monotonic() + 300
    while time.monotonic() < limite:
        st, _ = grafana(env, "GET", "/api/datasources/uid/zabbix")
        if st == 200:
            return
        time.sleep(3)
    raise Erro("O Grafana não respondeu em 5 minutos. Veja ./producao.sh logs grafana.")


def importa_dashboards(env, kits):
    if not DASHBOARDS.is_dir():
        diz(f"  --  pasta {DASHBOARDS.name}/ não encontrada: nenhum dashboard importado (importe pelo Grafana quando quiser)")
        return
    for arq in sorted(DASHBOARDS.glob("*/*.json")):
        kit = next((k for k in kits if PREFIXO_DASH[k] in arq.parent.name), None)
        if not kit:
            continue
        d = json.loads(arq.read_text(encoding="utf-8"))
        d.pop("id", None)
        st, _ = grafana(env, "GET", f"/api/dashboards/uid/{d['uid']}")
        if st == 200:
            diz(f"  --  {d['title']}: já existe (mantido como está, com as suas alterações)")
            continue
        st, r = grafana(env, "POST", "/api/dashboards/db", {"dashboard": d, "overwrite": False, "folderUid": ""})
        if st != 200:
            raise Erro(f"Grafana recusou o dashboard {d['title']}: {r.get('message', st)}")
        diz(f"  OK  dashboard {d['title']}")


def teste_datasource(env):
    st, r = grafana(env, "GET", "/api/datasources/uid/zabbix/health")
    return st == 200 and r.get("status") == "OK", r.get("message", "")


# ---------------------------------------------------------------- comandos

def mostra_acesso(env):
    host = ip_da_maquina() if env.get("PROD_BIND", "0.0.0.0") == "0.0.0.0" else endereco_local(env)
    diz()
    diz(f"Zabbix : http://{host}:{env['PROD_ZBX_PORT']}   usuário Admin   senha {env['PROD_ADMIN_PASSWORD']}")
    diz(f"Grafana: http://{host}:{env['PROD_GRAFANA_PORT']}   usuário admin   senha {env['PROD_GRAFANA_PASSWORD']}")
    diz(f"Agentes ativos e proxies: {host}:{env['PROD_ZBX_SERVER_PORT']}")


def cmd_up(args):
    exige_docker()
    pedidos = [k for k in KITS if getattr(args, k)]
    if not pedidos and not ENV.exists():
        raise Erro("Escolha o(s) kit(s) comprado(s): ./producao.sh up --provedor, --msp ou os dois.")
    verifica_arquivos(pedidos or kits_ativos(le_env()))
    env = inicializa(pedidos)
    kits = kits_ativos(env)
    verifica_arquivos(kits)
    diz(f"Kits desta instalação: {', '.join(NOME_KIT[k] for k in kits)}")
    diz("Baixando as imagens e subindo os containers...")
    compose("up", "-d", "--remove-orphans")
    api, versao = espera_zabbix(env)
    diz(f"Zabbix {versao} no ar. Configurando:")
    importa_templates(api, kits)
    configura_zabbix(api, env, kits, args.community)
    diz("Grafana:")
    espera_grafana(env)
    ok, msg = teste_datasource(env)
    diz(f"  {'OK ' if ok else 'ERRO'} datasource Zabbix {'conectado' if ok else '- ' + msg}")
    importa_dashboards(env, kits)
    mostra_acesso(env)
    diz()
    diz("Pronto. Próximo passo: cadastre os equipamentos (Coleta de dados → Hosts) com os templates do LEIA-ME.")
    diz("Faça backup com ./producao.sh backup (e guarde o arquivo fora deste servidor).")


def cmd_status(args):
    env = le_env()
    exige_docker()
    diz(f"Kits: {', '.join(NOME_KIT[k] for k in kits_ativos(env))}")
    diz(compose("ps", "--format", "  {{.Service}}\t{{.Status}}", capturar=True).stdout.rstrip() or "  nenhum container rodando")
    try:
        api = Zabbix(env)
        versao = api.call("apiinfo.version", auth=False)
        api.login(env["PROD_ADMIN_PASSWORD"])
        hosts = api.call("host.get", {"output": ["hostid"], "filter": {"status": 0}, "countOutput": True})
        sem = api.call("host.get", {"output": ["host"], "filter": {"status": 0},
                                    "selectInterfaces": ["available", "type"]})
        fora = [h["host"] for h in sem if any(i["available"] == "2" for i in h["interfaces"])]
        nao_sup = api.call("item.get", {"countOutput": True, "monitored": True, "filter": {"state": 1}})
        problemas = api.call("problem.get", {"countOutput": True})
        diz(f"Zabbix {versao}: {hosts} hosts ativos, {problemas} alertas abertos, {nao_sup} itens sem suporte")
        if fora:
            diz(f"  sem resposta: {', '.join(fora[:15])}{' ...' if len(fora) > 15 else ''}")
    except Erro as e:
        diz(f"Zabbix: {e}")
    ok, msg = teste_datasource(env)
    diz(f"Grafana: datasource Zabbix {'OK' if ok else 'com erro: ' + msg}")
    ultimos = sorted(BACKUPS.glob("*.tar.gz")) if BACKUPS.exists() else []
    diz(f"Último backup: {ultimos[-1].name if ultimos else 'nenhum (rode ./producao.sh backup)'}")
    mostra_acesso(env)


def cmd_creds(args):
    mostra_acesso(le_env())


def cmd_logs(args):
    le_env()
    compose("logs", "--tail", "200", *([args.servico] if args.servico else []))


def cmd_down(args):
    le_env()
    compose("stop")
    diz("Containers parados. Os dados continuam salvos; ./producao.sh up volta tudo.")


def faz_backup(env, rotulo=""):
    BACKUPS.mkdir(mode=0o700, exist_ok=True)
    nome = BACKUPS / f"nototi-producao-{time.strftime('%Y%m%d-%H%M%S')}{rotulo}.tar.gz"
    with tempfile.TemporaryDirectory(dir=AQUI) as tmp:
        tmp = Path(tmp)
        diz("  banco do Zabbix (pg_dump)...")
        with open(tmp / "zabbix.dump", "wb") as f:
            compose("exec", "-T", "db", "pg_dump", "-U", "zabbix", "-Fc", "zabbix", saida=f)
        diz("  Grafana (dashboards, usuários, configurações)...")
        with open(tmp / "grafana.tar.gz", "wb") as f:
            compose("exec", "-T", "grafana", "tar", "czf", "-", "-C", "/var/lib/grafana", ".", saida=f)
        shutil.copy(ENV, tmp / "env")
        (tmp / "versao.json").write_text(json.dumps({"criado": time.strftime("%Y-%m-%d %H:%M:%S"), "kits": kits_ativos(env)}))
        fd = os.open(nome, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f, tarfile.open(fileobj=f, mode="w:gz") as t:
            for p in sorted(tmp.iterdir()):
                t.add(p, arcname=p.name)
    tam = nome.stat().st_size / 1e6
    diz(f"Backup criado: {nome.relative_to(AQUI)} ({tam:.1f} MB)")
    diz("Ele contém as senhas desta instalação: guarde em local seguro, fora deste servidor.")
    return nome


def cmd_backup(args):
    env = le_env()
    exige_docker()
    faz_backup(env)


def cmd_restore(args):
    env = le_env()
    exige_docker()
    arq = Path(args.arquivo).expanduser().resolve()
    if not arq.is_file():
        raise Erro(f"Arquivo não encontrado: {args.arquivo}")
    with tarfile.open(arq) as t:
        nomes = set(t.getnames())
    if not {"zabbix.dump", "grafana.tar.gz", "env"} <= nomes:
        raise Erro(f"{arq.name} não é um backup do ./producao.sh.")
    confirma(f"o restore SUBSTITUI todo o Zabbix (hosts, histórico, alertas) e o Grafana deste servidor\n"
             f"  pelo conteúdo de {arq.name}.")
    with tempfile.TemporaryDirectory(dir=AQUI) as tmp:
        tmp = Path(tmp)
        with tarfile.open(arq) as t:
            try:
                t.extractall(tmp, filter="data")
            except TypeError:          # Python < 3.12
                t.extractall(tmp)
        diz("Parando Zabbix e Grafana...")
        compose("stop", "zabbix-server", "zabbix-web", "grafana")
        compose("up", "-d", "db")
        diz("Restaurando o banco do Zabbix...")
        psql = ["exec", "-T", "db", "psql", "-v", "ON_ERROR_STOP=1", "-U", "zabbix", "-d", "postgres", "-c"]
        compose(*psql, "DROP DATABASE IF EXISTS zabbix WITH (FORCE)", capturar=True)
        compose(*psql, "CREATE DATABASE zabbix OWNER zabbix", capturar=True)
        compose("exec", "-T", "db", "pg_restore", "-U", "zabbix", "-d", "zabbix", "--no-owner", "--exit-on-error",
                entrada=(tmp / "zabbix.dump").read_bytes(), saida=subprocess.DEVNULL)
        diz("Restaurando o Grafana...")
        compose("run", "--rm", "--no-deps", "-T", "--entrypoint", "sh", "grafana", "-c",
                "find /var/lib/grafana -mindepth 1 -delete && tar xzf - -C /var/lib/grafana",
                entrada=(tmp / "grafana.tar.gz").read_bytes(), saida=subprocess.DEVNULL)
        # senhas que moram dentro do banco/Grafana restaurados vêm do backup; banco, portas e rede ficam as deste servidor
        antigo = dict(l.split("=", 1) for l in (tmp / "env").read_text().splitlines() if l and not l.startswith("#") and "=" in l)
        for k in ("PROD_ADMIN_PASSWORD", "PROD_GRAFANA_PASSWORD", "PROD_ZBX_GRAFANA_PASSWORD", "PROD_KITS"):
            if k in antigo:
                env[k] = antigo[k]
        grava_env(env)
    compose("up", "-d", "--remove-orphans")
    espera_zabbix(env)
    espera_grafana(env)
    diz("Restore concluído. As senhas de acesso agora são as do backup:")
    mostra_acesso(env)


def cmd_update(args):
    env = le_env()
    exige_docker()
    diz("Backup antes de atualizar:")
    faz_backup(env, "-antes-update")
    diz("Baixando as versões novas (Zabbix 7.0 LTS e Grafana fixos nesta série)...")
    compose("pull")
    compose("up", "-d", "--remove-orphans")
    _, versao = espera_zabbix(env)
    espera_grafana(env)
    diz(f"Atualizado. Zabbix {versao}.")


def cmd_destroy(args):
    exige_docker()
    if not ENV.exists():
        raise Erro("Nada instalado nesta pasta.")
    confirma("isto APAGA a instalação de produção inteira: Zabbix (hosts, templates, histórico, alertas),\n"
             "  Grafana (dashboards, usuários) e o .env com as senhas.")
    compose("down", "-v", "--remove-orphans")
    ENV.unlink()
    diz("Instalação removida. Os backups em producao/backups/ foram mantidos.")


def main():
    p = argparse.ArgumentParser(prog="producao.sh", description="NOTO TI - Zabbix + Grafana em produção (Kit NOC e Kit Provedor)")
    sub = p.add_subparsers(dest="comando", required=True)
    up = sub.add_parser("up", help="instala ou atualiza a configuração (não apaga nada)")
    up.add_argument("--msp", action="store_true", help="Kit NOC: templates NOTO - LINK INTERNET / NOTO - VPN e dashboards Kit NOC")
    up.add_argument("--provedor", action="store_true", help="Kit Provedor: templates NOTO TI e dashboards Kit Provedor")
    up.add_argument("--community", help="community SNMP dos equipamentos (macro global {$SNMP_COMMUNITY})")
    up.set_defaults(func=cmd_up)
    sub.add_parser("status", help="containers, Zabbix, Grafana e último backup").set_defaults(func=cmd_status)
    sub.add_parser("creds", help="endereços e senhas").set_defaults(func=cmd_creds)
    lg = sub.add_parser("logs", help="logs: logs [db|zabbix-server|zabbix-web|grafana]")
    lg.add_argument("servico", nargs="?")
    lg.set_defaults(func=cmd_logs)
    sub.add_parser("backup", help="backup do Zabbix e do Grafana em producao/backups/").set_defaults(func=cmd_backup)
    rs = sub.add_parser("restore", help="restaura um backup (pede confirmação)")
    rs.add_argument("arquivo")
    rs.set_defaults(func=cmd_restore)
    sub.add_parser("update", help="faz backup e atualiza dentro do Zabbix 7.0 LTS").set_defaults(func=cmd_update)
    sub.add_parser("down", help="para os containers (mantém os dados)").set_defaults(func=cmd_down)
    sub.add_parser("destroy", help="apaga a instalação inteira (pede confirmação)").set_defaults(func=cmd_destroy)
    args = p.parse_args()
    try:
        args.func(args)
    except Erro as e:
        diz(f"ERRO: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        diz()
        sys.exit(130)


if __name__ == "__main__":
    main()
