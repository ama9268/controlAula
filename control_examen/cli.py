"""Interfaz de línea de órdenes."""
from __future__ import annotations

import argparse
import getpass
import sys
import threading
from typing import List, Optional

from . import acciones, config
from .config import ErrorConfig
from .remoto import Resultado

_salida = threading.Lock()


def _linea(r: Resultado) -> str:
    detalle = r.estado + (f" hasta {r.hasta}" if r.hasta else "")
    if r.mensaje:
        detalle = f"{detalle}  {r.mensaje}".strip()
    return f"  {r.ip:<16} {'OK' if r.ok else 'FALLO':<6} {detalle}"


def _al_terminar(r: Resultado) -> None:
    with _salida:
        print(_linea(r), flush=True)


def _resumen(resultados: List[Resultado]) -> int:
    fallidos = [r.ip for r in resultados if not r.ok]
    print(f"\n{len(resultados) - len(fallidos)} OK, {len(fallidos)} con fallo.")
    if fallidos:
        print("Fallidos: " + " ".join(fallidos))
    return 1 if fallidos else 0


def _confirmar(pregunta: str) -> bool:
    try:
        return input(f"{pregunta} [s/N] ").strip().lower() in ("s", "si", "sí")
    except EOFError:
        return False


def _pedir_clave(usuario: str) -> str:
    clave = getpass.getpass(f"Contraseña de sudo de {usuario} en los alumnos: ")
    if not clave:
        raise ErrorConfig("No se ha introducido la contraseña")
    return clave


def _crear_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="control-examen",
        description="Bloquea la red de los PCs de alumnos durante un examen. Sin argumentos abre la ventana.")
    sub = parser.add_subparsers(dest="accion", required=True)
    sub.add_parser("aulas", help="lista las aulas configuradas")

    objetivo = argparse.ArgumentParser(add_help=False)
    objetivo.add_argument("--aula", help="clave o nombre del aula configurada")
    objetivo.add_argument("--inicio", default="", help="IP inicial del rango")
    objetivo.add_argument("--fin", default="", help="IP final del rango")
    objetivo.add_argument("--profesor", default="", help="IP del PC del profesor (por defecto la del aula o .253)")
    confirmar = argparse.ArgumentParser(add_help=False)
    confirmar.add_argument("-s", "--si", action="store_true", help="no pedir confirmación")

    sub.add_parser("comprobar", parents=[objetivo], help="comprueba acceso SSH y nftables (sin sudo)")
    sub.add_parser("estado", parents=[objetivo], help="muestra si cada equipo está bloqueado (sin sudo)")
    bloq = sub.add_parser("bloquear", parents=[objetivo, confirmar], help="activa el bloqueo de examen")
    bloq.add_argument("--horas", type=float, help="autodesbloqueo pasadas estas horas")
    sub.add_parser("desbloquear", parents=[objetivo, confirmar], help="retira el bloqueo de examen")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _crear_parser().parse_args(argv)
    try:
        cfg = config.cargar()
        if args.accion == "aulas":
            if not cfg.aulas:
                print(f"No hay aulas configuradas en {config.RUTA_POR_DEFECTO}")
            for a in cfg.aulas.values():
                print(f"  {a.clave:<12} {a.nombre:<20} {a.inicio} – {a.fin}  profesor {a.profesor}")
                if a.permitidas is not None:
                    print(f"{'':15}salidas: {', '.join(a.permitidas)}")
                if a.puertos is not None:
                    print(f"{'':15}entradas: {', '.join(a.puertos)}")
            return 0

        obj = config.resolver_objetivo(cfg, args.aula, args.inicio, args.fin, args.profesor)
        if args.accion == "bloquear":
            horas = config.validar_horas(args.horas if args.horas is not None else cfg.horas)
        print(f"{args.accion.capitalize()} — {obj.descripcion}")

        if args.accion in ("comprobar", "estado"):
            funcion = acciones.comprobar if args.accion == "comprobar" else acciones.estado
            resultados = funcion(obj.ips, cfg, _al_terminar)
        else:
            if args.accion == "bloquear":
                print(f"Salidas permitidas: profesor {obj.profesor}, {', '.join(obj.permitidas)}")
                print(f"Entradas permitidas: {', '.join(obj.puertos)}")
                print(f"Autodesbloqueo en {horas:g} h")
                locales = config.ips_locales()
                if locales and obj.profesor not in locales:
                    print(f"AVISO: este PC no tiene la IP {obj.profesor}; los alumnos no podrán alcanzarlo.")
            if not args.si and not _confirmar("¿Continuar?"):
                print("Cancelado.")
                return 1
            clave = _pedir_clave(cfg.usuario)
            if args.accion == "bloquear":
                resultados = acciones.bloquear(obj.ips, cfg, clave, obj, horas, _al_terminar)
            else:
                resultados = acciones.desbloquear(obj.ips, cfg, clave, _al_terminar)
            del clave

        acciones.registrar(args.accion, obj, resultados)
        return _resumen(resultados)
    except ErrorConfig as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrumpido.", file=sys.stderr)
        return 130
