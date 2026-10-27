"""Ejecución de órdenes por SSH en los PCs de los alumnos, en paralelo."""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

TIEMPO_MAXIMO = 60  # segundos por equipo

# BatchMode: nunca se queda esperando una contraseña SSH (las claves ya están
# distribuidas). accept-new: acepta equipos nuevos pero rechaza claves de host
# que hayan cambiado. -a -x: sin reenvío de agente ni X11 hacia los alumnos.
OPCIONES_SSH = [
    "-T", "-a", "-x",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=5",
    "-o", "ServerAliveInterval=5",
    "-o", "ServerAliveCountMax=3",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "LogLevel=ERROR",
]

RUTA_AGENTE = "/usr/local/sbin/examen-fw"

# Se ejecuta con sudo en el alumno: instala el agente como root, comprueba que
# es exactamente el enviado y lo ejecuta con el resto de argumentos.
INSTALADOR = (
    't=$1; h=$2; shift 2; '
    f'install -o root -g root -m 0755 "$t" {RUTA_AGENTE} || exit 90; '
    f'printf "%s  %s\\n" "$h" {RUTA_AGENTE} | sha256sum -c --status - '
    '|| { echo "ERROR=el agente instalado no coincide con el enviado"; exit 91; }; '
    f'exec {RUTA_AGENTE} "$@"'
)

ERRORES_CONOCIDOS = [
    ("Host key verification failed", "La clave de host ha cambiado (posible suplantación); revisa known_hosts"),
    ("REMOTE HOST IDENTIFICATION HAS CHANGED", "La clave de host ha cambiado (posible suplantación); revisa known_hosts"),
    ("Permission denied", "El equipo no acepta la clave SSH del profesor"),
    ("Connection timed out", "Sin respuesta (¿apagado?)"),
    ("Operation timed out", "Sin respuesta (¿apagado?)"),
    ("No route to host", "Sin ruta al equipo (¿apagado?)"),
    ("Connection refused", "SSH no está activo en el equipo"),
    ("incorrect password", "Contraseña de sudo incorrecta"),
    ("no password was provided", "Contraseña de sudo incorrecta"),
    ("is not in the sudoers file", "El usuario no tiene permisos de sudo"),
]


@dataclass
class Resultado:
    ip: str
    ok: bool
    mensaje: str = ""
    datos: Dict[str, str] = field(default_factory=dict)

    @property
    def estado(self) -> str:
        return self.datos.get("ESTADO", "")

    @property
    def hasta(self) -> str:
        return self.datos.get("HASTA", "")


def comando_sudo(agente: bytes, argumentos: List[str]) -> str:
    """Orden remota que envía el agente embebido y lo ejecuta con sudo.

    La contraseña de sudo no aparece aquí: se envía por la entrada estándar.
    """
    b64 = base64.b64encode(agente).decode("ascii")
    sha = hashlib.sha256(agente).hexdigest()
    return (
        "t=$(mktemp) || exit 1; trap 'rm -f \"$t\"' EXIT; "
        f'printf %s {b64} | base64 -d > "$t" && '
        f"LC_ALL=C sudo -S -p '' sh -c {shlex.quote(INSTALADOR)} examen-fw \"$t\" {sha} "
        + " ".join(shlex.quote(a) for a in argumentos)
    )


def ocultar(texto: str, secreto: Optional[str]) -> str:
    return texto.replace(secreto, "***") if secreto else texto


def interpretar(ip: str, codigo: int, salida: str, errores: str) -> Resultado:
    datos = {}
    for linea in salida.splitlines():
        clave, igual, valor = linea.partition("=")
        if igual and clave.isupper():
            datos[clave] = valor.strip()

    if codigo == 0 and "ERROR" not in datos:
        return Resultado(ip, True, "", datos)
    if "ERROR" in datos:
        return Resultado(ip, False, datos["ERROR"], datos)
    for patron, mensaje in ERRORES_CONOCIDOS:
        if patron in errores:
            return Resultado(ip, False, mensaje, datos)
    ultima = (errores.strip() or salida.strip()).splitlines()[-1:] or [f"código {codigo}"]
    return Resultado(ip, False, ultima[0], datos)


def ejecutar(ip: str, usuario: str, orden: str, entrada: str = "",
             secreto: Optional[str] = None) -> Resultado:
    """Ejecuta `orden` en el equipo. `entrada` va por stdin (nunca por argv)."""
    try:
        proceso = subprocess.run(
            ["ssh", *OPCIONES_SSH, f"{usuario}@{ip}", orden],
            input=entrada, capture_output=True, text=True,
            timeout=TIEMPO_MAXIMO, check=False,
        )
    except subprocess.TimeoutExpired:
        return Resultado(ip, False, "Tiempo agotado")
    except OSError as e:
        return Resultado(ip, False, f"No se pudo ejecutar ssh: {e}")
    return interpretar(ip, proceso.returncode,
                       ocultar(proceso.stdout, secreto), ocultar(proceso.stderr, secreto))


def en_paralelo(ips: List[str], tarea: Callable[[str], Resultado], hilos: int,
                al_terminar: Optional[Callable[[Resultado], None]] = None) -> List[Resultado]:
    """Ejecuta `tarea(ip)` en paralelo. Llama a `al_terminar` según acaba cada equipo."""
    resultados = []
    with ThreadPoolExecutor(max_workers=max(1, min(hilos, len(ips)))) as ejecutor:
        futuros = {ejecutor.submit(tarea, ip): ip for ip in ips}
        for futuro in as_completed(futuros):
            try:
                resultado = futuro.result()
            except Exception as e:  # un fallo en un equipo no detiene al resto
                resultado = Resultado(futuros[futuro], False, f"Error interno: {e}")
            resultados.append(resultado)
            if al_terminar:
                al_terminar(resultado)
    return sorted(resultados, key=lambda r: ipaddress.ip_address(r.ip))
