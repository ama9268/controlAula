import tempfile
import unittest
from pathlib import Path
from unittest import mock

from control_examen import config
from control_examen.config import ErrorConfig


class TestRangos(unittest.TestCase):
    def test_expandir(self):
        self.assertEqual(config.expandir_rango("10.14.14.20", "10.14.14.22"),
                         ["10.14.14.20", "10.14.14.21", "10.14.14.22"])

    def test_una_ip(self):
        self.assertEqual(config.expandir_rango("10.14.14.20", "10.14.14.20"), ["10.14.14.20"])

    def test_excluir(self):
        self.assertEqual(config.expandir_rango("10.1.1.1", "10.1.1.3", excluir={"10.1.1.2"}),
                         ["10.1.1.1", "10.1.1.3"])

    def test_invertido(self):
        with self.assertRaises(ErrorConfig):
            config.expandir_rango("10.14.14.30", "10.14.14.20")

    def test_distinta_red(self):
        with self.assertRaises(ErrorConfig):
            config.expandir_rango("10.14.14.20", "10.14.15.20")

    def test_ip_no_valida(self):
        for mala in ("10.14.14", "10.14.14.300", "10.14.14.1; rm -rf /", "", "::1"):
            with self.assertRaises(ErrorConfig, msg=mala):
                config.validar_ip(mala)

    def test_profesor_por_defecto(self):
        self.assertEqual(config.profesor_por_defecto("10.14.14.20"), "10.14.14.253")

    def test_minutos(self):
        self.assertEqual(config.a_minutos(4), 240)
        self.assertEqual(config.a_minutos(0.001), 1)
        for malo in (0, -1, 25):
            with self.assertRaises(ErrorConfig):
                config.a_minutos(malo)


@mock.patch.object(config, "ips_locales", return_value={"10.14.14.253"})
class TestObjetivo(unittest.TestCase):
    def setUp(self):
        self.cfg = config.Config(aulas={"a14": config.Aula("a14", "Aula 14", "10.14.14.250",
                                                           "10.14.14.254", "10.14.14.253")})

    def test_excluye_profesor(self, _):
        obj = config.resolver_objetivo(self.cfg, "Aula 14")
        self.assertEqual(obj.ips, ["10.14.14.250", "10.14.14.251", "10.14.14.252", "10.14.14.254"])
        self.assertEqual(obj.profesor, "10.14.14.253")

    def test_rango_manual_tiene_prioridad(self, _):
        obj = config.resolver_objetivo(self.cfg, "a14", inicio="10.14.14.251", fin="10.14.14.251")
        self.assertEqual(obj.ips, ["10.14.14.251"])

    def test_sin_datos(self, _):
        with self.assertRaises(ErrorConfig):
            config.resolver_objetivo(self.cfg)

    def test_aula_inexistente(self, _):
        with self.assertRaises(ErrorConfig):
            config.resolver_objetivo(self.cfg, "aula99")


class TestCargar(unittest.TestCase):
    def _cargar(self, texto):
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "aulas.ini"
            ruta.write_text(texto, encoding="utf-8")
            return config.cargar(ruta)

    def test_ejemplo(self):
        cfg = config.cargar(Path(__file__).parent.parent / "aulas.ini.ejemplo")
        self.assertEqual(cfg.usuario, "profesor")
        self.assertEqual(cfg.permitidas, ["10.0.0.4", "10.0.0.2"])
        self.assertEqual(cfg.puertos, [22, 11100])
        self.assertEqual(cfg.aulas["aula14"].profesor, "10.14.14.253")

    def test_sin_fichero(self):
        cfg = config.cargar(Path("/no/existe.ini"))
        self.assertEqual(cfg.aulas, {})

    def test_profesor_opcional(self):
        cfg = self._cargar("[a1]\ninicio = 10.1.2.10\nfin = 10.1.2.20\n")
        self.assertEqual(cfg.aulas["a1"].profesor, "10.1.2.253")

    def test_fuerza_ssh(self):
        cfg = self._cargar("[general]\npuertos_entrada = 11100\n")
        self.assertEqual(cfg.puertos, [22, 11100])

    def test_errores(self):
        for texto in ("[general]\nusuario = pro;fesor x\n",
                      "[general]\nusuario = $(id)\n",
                      "[general]\npermitidas = 10.0.0.4, malo\n",
                      "[general]\npuertos_entrada = 22, 70000\n",
                      "[general]\nhoras_autodesbloqueo = 30\n",
                      "[a1]\ninicio = 10.1.2.10\n",
                      "[a1]\ninicio = 10.1.2.10\nfin = 10.1.3.20\n"):
            with self.assertRaises(ErrorConfig, msg=texto):
                self._cargar(texto)


if __name__ == "__main__":
    unittest.main()
