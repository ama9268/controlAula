"""Acciones sobre los equipos de un aula: comprobar, estado, bloquear y desbloquear."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, List, Optional

from . import config, remoto
from .config import Config, Objetivo
from .remoto import Resultado

AGENTE = (Path(__file__).parent / "agente" / "examen-fw.sh").read_bytes()
REGISTRO = Path.home() / ".local" / "state" / "control-examen" / "registro.log"

# Sin sudo: lo que puede leer cualquier usuario en el alumno.
CONSULTA = (
    'if [ -x /usr/sbin/nft ]; then echo NFT=si; else echo NFT=no; fi; '
    'if systemctl is-active --quiet examen-fw.service && [ -r /etc/examen-fw/limite ]; then '
    'echo ESTADO=BLOQUEADO; echo "HASTA=$(date -d "@$(cat /etc/examen-fw/limite)" "+%d/%m %H:%M")"; '
    'else echo ESTADO=LIBRE; fi'
)

AlTerminar = Optional[Callable[[Resultado], None]]


def _consultar(ips: List[str], cfg: Config, al_terminar: AlTerminar,
               exigir_nft: bool) -> List[Resultado]:
    def tarea(ip: str) -> Resultado:
        r = remoto.ejecutar(ip, cfg.usuario, CONSULTA)
        if r.ok and exigir_nft and r.datos.get("NFT") != "si":
            r.ok, r.mensaje = False, "nftables (nft) no está instalado"
        return r
    return remoto.en_paralelo(ips, tarea, cfg.paralelo, al_terminar)


def comprobar(ips: List[str], cfg: Config, al_terminar: AlTerminar = None) -> List[Resultado]:
    """Acceso con clave SSH, nftables disponible y estado actual (sin sudo)."""
    return _consultar(ips, cfg, al_terminar, exigir_nft=True)


def estado(ips: List[str], cfg: Config, al_terminar: AlTerminar = None) -> List[Resultado]:
    """Estado del bloqueo según systemd (sin sudo)."""
    return _consultar(ips, cfg, al_terminar, exigir_nft=False)


def _con_sudo(ips: List[str], cfg: Config, clave: str, argumentos: List[str],
              al_terminar: AlTerminar) -> List[Resultado]:
    orden = remoto.comando_sudo(AGENTE, argumentos)
    entrada = clave + "\n"
    return remoto.en_paralelo(
        ips, lambda ip: remoto.ejecutar(ip, cfg.usuario, orden, entrada, secreto=clave),
        cfg.paralelo, al_terminar)


def argumentos_bloqueo(objetivo: Objetivo, horas: float) -> List[str]:
    """Argumentos del agente. El PC del profesor siempre es un destino permitido."""
    permitidas = list(dict.fromkeys([config.validar_ip(objetivo.profesor), *objetivo.permitidas]))
    return ["bloquear",
            "--permitidas", ",".join(permitidas),
            "--puertos", ",".join(objetivo.puertos),
            "--minutos", str(config.a_minutos(horas))]


def bloquear(ips: List[str], cfg: Config, clave: str, objetivo: Objetivo, horas: float,
             al_terminar: AlTerminar = None) -> List[Resultado]:
    return _con_sudo(ips, cfg, clave, argumentos_bloqueo(objetivo, horas), al_terminar)


def desbloquear(ips: List[str], cfg: Config, clave: str,
                al_terminar: AlTerminar = None) -> List[Resultado]:
    return _con_sudo(ips, cfg, clave, ["desbloquear"], al_terminar)


def registrar(accion: str, objetivo: Objetivo, resultados: List[Resultado]) -> None:
    """Añade la acción al registro local (sin contraseñas). Nunca hace fallar la acción."""
    try:
        REGISTRO.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(REGISTRO, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            fallos = sum(not r.ok for r in resultados)
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {accion} {objetivo.descripcion} "
                    f"profesor={objetivo.profesor} fallos={fallos}\n")
            for r in resultados:
                f.write(f"    {r.ip} {'OK' if r.ok else 'FALLO'} {r.estado} {r.mensaje}\n")
    except OSError:
        pass
