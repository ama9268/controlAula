import base64
import hashlib
import shlex
import subprocess
import unittest
from pathlib import Path

from control_examen import acciones, config, remoto

AGENTE = Path(__file__).parent.parent / "control_examen" / "agente" / "examen-fw.sh"


class TestInterpretar(unittest.TestCase):
    def test_ok(self):
        r = remoto.interpretar("10.0.0.1", 0, "ESTADO=BLOQUEADO\nHASTA=27/10 13:45\n", "")
        self.assertTrue(r.ok)
        self.assertEqual((r.estado, r.hasta), ("BLOQUEADO", "27/10 13:45"))

    def test_error_del_agente(self):
        r = remoto.interpretar("10.0.0.1", 1, "ERROR=nftables (nft) no está instalado\n", "")
        self.assertFalse(r.ok)
        self.assertIn("nftables", r.mensaje)

    def test_error_aunque_codigo_cero(self):
        self.assertFalse(remoto.interpretar("10.0.0.1", 0, "ERROR=x\n", "").ok)

    def test_errores_conocidos(self):
        casos = {
            "Host key verification failed.": "clave de host",
            "profesor@10.0.0.1: Permission denied (publickey).": "clave SSH",
            "sudo: 1 incorrect password attempt": "sudo incorrecta",
        }
        for errores, esperado in casos.items():
            r = remoto.interpretar("10.0.0.1", 255, "", errores)
            self.assertFalse(r.ok)
            self.assertIn(esperado, r.mensaje)

    def test_ocultar(self):
        self.assertEqual(remoto.ocultar("eco s3cr3t!", "s3cr3t!"), "eco ***")
        self.assertEqual(remoto.ocultar("texto", ""), "texto")


class TestComandoSudo(unittest.TestCase):
    def test_estructura(self):
        agente = b"#!/bin/bash\necho hola\n"
        orden = remoto.comando_sudo(agente, ["bloquear", "--permitidas", "10.0.0.4"])
        self.assertIn(base64.b64encode(agente).decode(), orden)
        self.assertIn(hashlib.sha256(agente).hexdigest(), orden)
        self.assertIn("sudo -S -p ''", orden)
        self.assertTrue(orden.endswith("bloquear --permitidas 10.0.0.4"))

    def test_argumentos_citados(self):
        orden = remoto.comando_sudo(b"x", ["a; reboot"])
        self.assertTrue(orden.endswith(shlex.quote("a; reboot")))

    def test_argumentos_bloqueo(self):
        cfg = config.Config()
        args = acciones.argumentos_bloqueo(cfg, "10.14.14.253", 2)
        self.assertEqual(args, ["bloquear", "--permitidas", "10.14.14.253,10.0.0.4,10.0.0.2",
                                "--puertos", "22,11100", "--minutos", "120"])

    def test_profesor_no_se_duplica(self):
        args = acciones.argumentos_bloqueo(config.Config(), "10.0.0.4", 1)
        self.assertEqual(args[2], "10.0.0.4,10.0.0.2")


class TestAgente(unittest.TestCase):
    """Validación y reglas del agente (generar-nft no necesita root ni nft)."""

    def _agente(self, *args):
        return subprocess.run(["bash", str(AGENTE), *args], capture_output=True, text=True)

    def test_reglas(self):
        p = self._agente("generar-nft", "--permitidas", "10.14.14.253,10.0.0.4,10.0.0.2",
                         "--puertos", "22,11100")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("ip daddr { 10.14.14.253, 10.0.0.4, 10.0.0.2 } accept", p.stdout)
        self.assertIn("tcp dport { 22, 11100 } accept", p.stdout)
        self.assertIn("policy drop", p.stdout)
        self.assertIn("ct direction reply accept", p.stdout)

    def test_rechaza_valores_maliciosos_o_erroneos(self):
        casos = [
            ("--permitidas", "10.0.0.4 } accept; flush ruleset", "--puertos", "22"),
            ("--permitidas", "10.0.0.4,", "--puertos", "22"),
            ("--permitidas", "10.0.0.04", "--puertos", "22"),
            ("--permitidas", "10.0.0.256", "--puertos", "22"),
            ("--permitidas", "10.0.0.4", "--puertos", "11100"),
            ("--permitidas", "10.0.0.4", "--puertos", "22,022"),
            ("--permitidas", "10.0.0.4", "--puertos", "22,70000"),
            ("--permitidas", "10.0.0.4", "--puertos"),
            ("--otra", "x"),
        ]
        for args in casos:
            p = self._agente("generar-nft", *args)
            self.assertEqual(p.returncode, 1, args)
            self.assertTrue(p.stdout.startswith("ERROR="), args)

    def test_requiere_root(self):
        p = self._agente("bloquear", "--permitidas", "10.0.0.4", "--puertos", "22", "--minutos", "5")
        self.assertEqual(p.returncode, 1)
        self.assertIn("ERROR=se necesita root", p.stdout)

    def test_accion_desconocida(self):
        self.assertEqual(self._agente("borrar-todo").returncode, 2)


if __name__ == "__main__":
    unittest.main()
